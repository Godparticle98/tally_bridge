from __future__ import annotations

import json
from datetime import datetime, timedelta

import frappe
from frappe.utils import now_datetime

from .renderer import render_document
from .masters import render_master
from .utils import canonical_json


DEFAULT_MAPPINGS = [
    {"source_doctype": "Customer", "trigger_event": "after_insert", "object_type": "Ledger", "priority": 10},
    {"source_doctype": "Customer", "trigger_event": "on_update", "object_type": "Ledger", "priority": 10},
    {"source_doctype": "Supplier", "trigger_event": "after_insert", "object_type": "Ledger", "priority": 10},
    {"source_doctype": "Supplier", "trigger_event": "on_update", "object_type": "Ledger", "priority": 10},
    {"source_doctype": "Item", "trigger_event": "after_insert", "object_type": "Stock Item", "priority": 20},
    {"source_doctype": "UOM", "trigger_event": "after_insert", "object_type": "Unit", "priority": 20},
    {"source_doctype": "Account", "trigger_event": "after_insert", "object_type": "Ledger", "priority": 20},
    {"source_doctype": "Sales Invoice", "trigger_event": "on_submit", "object_type": "Voucher", "tally_voucher_type": "Sales", "priority": 100},
    {"source_doctype": "Sales Invoice", "trigger_event": "on_cancel", "object_type": "Voucher", "tally_voucher_type": "Sales", "priority": 100},
    {"source_doctype": "Sales Invoice", "trigger_event": "on_update_after_submit", "object_type": "Voucher", "tally_voucher_type": "Sales", "priority": 100},
    {"source_doctype": "Purchase Invoice", "trigger_event": "on_submit", "object_type": "Voucher", "tally_voucher_type": "Purchase", "priority": 100},
    {"source_doctype": "Purchase Invoice", "trigger_event": "on_cancel", "object_type": "Voucher", "tally_voucher_type": "Purchase", "priority": 100},
    {"source_doctype": "Purchase Invoice", "trigger_event": "on_update_after_submit", "object_type": "Voucher", "tally_voucher_type": "Purchase", "priority": 100},
    {"source_doctype": "Payment Entry", "trigger_event": "on_submit", "object_type": "Voucher", "priority": 100},
    {"source_doctype": "Payment Entry", "trigger_event": "on_cancel", "object_type": "Voucher", "priority": 100},
    {"source_doctype": "Journal Entry", "trigger_event": "on_submit", "object_type": "Voucher", "priority": 100},
    {"source_doctype": "Journal Entry", "trigger_event": "on_cancel", "object_type": "Voucher", "priority": 100},
]


def after_sync():
    """Seed app-owned defaults only after the app's DocTypes have been synchronized."""
    if not frappe.db.exists("Role", "Tally Bridge Agent"):
        frappe.get_doc({"doctype": "Role", "role_name": "Tally Bridge Agent", "desk_access": 0}).insert(ignore_permissions=True)

    for row in DEFAULT_MAPPINGS:
        if frappe.db.exists(
            "Tally DocType Mapping",
            {"source_doctype": row["source_doctype"], "trigger_event": row["trigger_event"]},
        ):
            continue

        frappe.get_doc({"doctype": "Tally DocType Mapping", "enabled": 1, **row}).insert(
            ignore_permissions=True
        )

    frappe.db.commit()


def claim_next_job(agent_id: str):
    """Atomically claim one queued job using an update conditioned on status=Queued."""
    now = now_datetime()
    # Recover leases left behind by a stopped agent.
    frappe.db.sql(
        """
        UPDATE `tabTally Sync Queue`
        SET status='Queued', claimed_by=NULL, claimed_at=NULL, lease_until=NULL
        WHERE status='Processing' AND lease_until IS NOT NULL AND lease_until < %(now)s
        """,
        {"now": now},
    )

    row = frappe.db.sql(
        """
        SELECT name FROM `tabTally Sync Queue`
        WHERE status = 'Queued'
          AND (next_attempt_at IS NULL OR next_attempt_at <= %(now)s)
        ORDER BY priority ASC, queued_at ASC
        LIMIT 1
        """,
        {"now": now},
        as_dict=True,
    )
    if not row:
        return None
    name = row[0]["name"]
    lease_until = now + timedelta(minutes=5)
    frappe.db.sql(
        """
        UPDATE `tabTally Sync Queue`
        SET status='Processing', claimed_by=%(agent)s, claimed_at=%(now)s,
            lease_until=%(lease_until)s, attempts=COALESCE(attempts,0)+1
        WHERE name=%(name)s AND status='Queued'
        """,
        {"agent": agent_id, "now": now, "lease_until": lease_until, "name": name},
    )
    frappe.db.commit()
    record = frappe.get_doc("Tally Sync Queue", name)
    if record.status != "Processing" or record.claimed_by != agent_id:
        return None
    return record


def build_tally_payload(queue_doc):
    connection = frappe.get_doc("Tally Connection Settings", queue_doc.connection)
    snapshot = json.loads(queue_doc.payload_json)
    doc = frappe.get_doc(snapshot)
    action = "Cancel" if queue_doc.event == "on_cancel" else "Alter" if queue_doc.event == "on_update_after_submit" else "Create"
    mapping_name = frappe.db.get_value(
        "Tally DocType Mapping",
        {"source_doctype": queue_doc.source_doctype, "trigger_event": queue_doc.event, "enabled": 1},
        "name",
    )
    if not mapping_name:
        frappe.throw(f"No active Tally mapping for {queue_doc.source_doctype} / {queue_doc.event}")
    mapping = frappe.get_doc("Tally DocType Mapping", mapping_name)
    if mapping.object_type in {"Ledger", "Stock Item", "Unit"}:
        return render_master(doc, company=connection.tally_company_name, action=action)
    return render_document(doc, company=connection.tally_company_name, action=action)


def complete_job(queue_name, success: bool, tally_response: str, http_status: int, latency_ms: float, error: str | None = None):
    q = frappe.get_doc("Tally Sync Queue", queue_name)
    if success:
        q.status = "Success"
    elif q.attempts >= 5:
        q.status = "Failed"
    else:
        q.status = "Queued"
    q.last_error = error or (None if success else tally_response[:1000])
    q.last_tally_response = tally_response
    q.last_http_status = http_status
    q.last_latency_ms = latency_ms
    q.completed_at = now_datetime() if success else None
    if not success and q.status == "Queued":
        q.next_attempt_at = now_datetime() + timedelta(minutes=min(30, 2 ** max(q.attempts - 1, 0)))
    q.claimed_by = None
    q.claimed_at = None
    q.lease_until = None
    q.save(ignore_permissions=True)

    log = frappe.get_doc(
        {
            "doctype": "Tally Sync Log",
            "queue": q.name,
            "connection": q.connection,
            "source_doctype": q.source_doctype,
            "source_name": q.source_name,
            "event": q.event,
            "status": "Success" if success else "Failed",
            "http_status": http_status,
            "latency_ms": latency_ms,
            "response_body": tally_response,
            "error_message": error,
            "attempt_no": q.attempts,
        }
    )
    log.insert(ignore_permissions=True)
    frappe.db.commit()
