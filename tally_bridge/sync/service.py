from __future__ import annotations

import json
from datetime import datetime, timedelta

import frappe
from frappe.utils import now_datetime

from .renderer import render_document
from .masters import render_master
from .utils import canonical_json, stable_remote_id


DEFAULT_MAPPINGS = [
    {"source_doctype": "Customer", "trigger_event": "after_insert", "object_type": "Ledger", "priority": 10},
    {"source_doctype": "Customer", "trigger_event": "on_update", "object_type": "Ledger", "priority": 10},
    {"source_doctype": "Supplier", "trigger_event": "after_insert", "object_type": "Ledger", "priority": 10},
    {"source_doctype": "Supplier", "trigger_event": "on_update", "object_type": "Ledger", "priority": 10},
    {"source_doctype": "Item", "trigger_event": "after_insert", "object_type": "Stock Item", "priority": 20},
    {"source_doctype": "Item", "trigger_event": "on_update", "object_type": "Stock Item", "priority": 20},
    {"source_doctype": "UOM", "trigger_event": "after_insert", "object_type": "Unit", "priority": 20},
    {"source_doctype": "UOM", "trigger_event": "on_update", "object_type": "Unit", "priority": 20},
    {"source_doctype": "Account", "trigger_event": "after_insert", "object_type": "Ledger", "priority": 20},
    {"source_doctype": "Account", "trigger_event": "on_update", "object_type": "Ledger", "priority": 20},
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


def _dependencies_state(queue_doc):
    if not queue_doc.depends_on:
        return True, False, None

    try:
        dependency_names = json.loads(queue_doc.depends_on)
    except (TypeError, ValueError):
        return False, True, "Invalid depends_on queue metadata"

    if not dependency_names:
        return True, False, None

    rows = frappe.get_all(
        "Tally Sync Queue",
        filters={"name": ["in", dependency_names]},
        fields=["name", "status", "source_doctype", "source_name"],
        limit_page_length=len(dependency_names),
    )
    states = {row.name: row for row in rows}
    missing = [name for name in dependency_names if name not in states]
    if missing:
        return False, True, f"Dependency queue job missing: {', '.join(missing)}"

    failed = [row for row in rows if row.status == "Failed"]
    if failed:
        names = ", ".join(
            f"{row.source_doctype}/{row.source_name}" for row in failed
        )
        return False, True, f"Dependency failed: {names}"

    ready = all(row.status == "Success" for row in rows)
    return ready, False, None if ready else "Waiting for master synchronization"


def claim_next_job(agent_id: str):
    """Claim the oldest eligible job whose explicit dependencies are complete."""
    now = now_datetime()

    frappe.db.sql(
        """
        UPDATE `tabTally Sync Queue`
        SET status='Queued', claimed_by=NULL, claimed_at=NULL, lease_until=NULL
        WHERE status='Processing'
          AND lease_until IS NOT NULL
          AND lease_until < %(now)s
        """,
        {"now": now},
    )

    candidates = frappe.db.sql(
        """
        SELECT name
        FROM `tabTally Sync Queue`
        WHERE status IN ('Queued', 'Blocked')
          AND (next_attempt_at IS NULL OR next_attempt_at <= %(now)s)
        ORDER BY priority ASC, queued_at ASC
        LIMIT 50
        """,
        {"now": now},
        as_dict=True,
    )

    selected = None

    for row in candidates:
        candidate = frappe.get_doc("Tally Sync Queue", row.name)
        ready, failed, message = _dependencies_state(candidate)

        if failed:
            candidate.status = "Blocked"
            candidate.last_error = message
            candidate.save(ignore_permissions=True)
            continue

        if not ready:
            candidate.status = "Blocked"
            candidate.last_error = message
            candidate.save(ignore_permissions=True)
            continue

        if candidate.status == "Blocked":
            candidate.status = "Queued"
            candidate.last_error = None
            candidate.save(ignore_permissions=True)

        selected = candidate.name
        break

    if not selected:
        frappe.db.commit()
        return None

    lease_until = now + timedelta(minutes=5)

    frappe.db.sql(
        """
        UPDATE `tabTally Sync Queue`
        SET status='Processing',
            claimed_by=%(agent)s,
            claimed_at=%(now)s,
            lease_until=%(lease_until)s,
            attempts=COALESCE(attempts,0)+1
        WHERE name=%(name)s AND status='Queued'
        """,
        {
            "agent": agent_id,
            "now": now,
            "lease_until": lease_until,
            "name": selected,
        },
    )
    frappe.db.commit()

    record = frappe.get_doc("Tally Sync Queue", selected)
    if record.status != "Processing" or record.claimed_by != agent_id:
        return None

    return record


def _master_identity(connection: str, source_doctype: str, source_name: str):
    rows = frappe.get_all(
        "Tally Master Identity",
        filters={
            "connection": connection,
            "source_doctype": source_doctype,
            "source_name": source_name,
        },
        fields=["name", "tally_name", "stable_remote_id", "status"],
        limit_page_length=1,
    )
    return rows[0] if rows else None


def _master_display_name(doc) -> str:
    if doc.doctype == "Customer":
        return getattr(doc, "customer_name", None) or doc.name
    if doc.doctype == "Supplier":
        return getattr(doc, "supplier_name", None) or doc.name
    if doc.doctype == "Item":
        return getattr(doc, "item_name", None) or doc.name
    if doc.doctype == "Account":
        return getattr(doc, "account_name", None) or doc.name
    return getattr(doc, "name", None) or str(doc)


def _master_object_type(source_doctype: str) -> str:
    return {
        "Customer": "Ledger",
        "Supplier": "Ledger",
        "Item": "Stock Item",
        "UOM": "Unit",
        "Account": "Ledger",
    }.get(source_doctype, "Ledger")


def build_tally_payload(queue_doc):
    connection = frappe.get_doc("Tally Connection Settings", queue_doc.connection)
    snapshot = json.loads(queue_doc.payload_json)
    doc = frappe.get_doc(snapshot)
    action = (
        "Cancel"
        if queue_doc.event == "on_cancel"
        else "Alter"
        if queue_doc.event in {"on_update", "on_update_after_submit"}
        else "Create"
    )
    mapping_name = frappe.db.get_value(
        "Tally DocType Mapping",
        {"source_doctype": queue_doc.source_doctype, "trigger_event": queue_doc.event, "enabled": 1},
        "name",
    )
    if not mapping_name:
        frappe.throw(f"No active Tally mapping for {queue_doc.source_doctype} / {queue_doc.event}")
    mapping = frappe.get_doc("Tally DocType Mapping", mapping_name)

    if mapping.object_type in {"Ledger", "Stock Item", "Unit"}:
        identity = _master_identity(connection.name, doc.doctype, doc.name)

        # Master events are synchronization intents. The identity record is the
        # authoritative link to the current Tally master name. If no identity
        # exists, CREATE even when ERPNext emitted on_update.
        if identity:
            action = "Alter"
            tally_name = identity.get("tally_name")
        elif queue_doc.event in {"on_update", "on_update_after_submit"}:
            # Backward-compatible migration path for masters synced by older
            # versions, where the Tally name was the ERPNext Doc ID.
            action = "Alter"
            tally_name = doc.name
        else:
            action = "Create"
            tally_name = None

        return render_master(
            doc,
            company=connection.tally_company_name,
            action=action,
            tally_name=tally_name,
        )

    return render_document(
        doc,
        company=connection.tally_company_name,
        action=action,
        connection=connection.name,
    )


def build_master_create_fallback(queue_doc):
    """Build a CREATE payload for a master whose legacy ALTER target is absent."""
    connection = frappe.get_doc("Tally Connection Settings", queue_doc.connection)
    snapshot = json.loads(queue_doc.payload_json)
    doc = frappe.get_doc(snapshot)
    mapping_name = frappe.db.get_value(
        "Tally DocType Mapping",
        {"source_doctype": queue_doc.source_doctype, "trigger_event": queue_doc.event, "enabled": 1},
        "name",
    )
    if not mapping_name:
        frappe.throw(f"No active Tally mapping for {queue_doc.source_doctype} / {queue_doc.event}")
    mapping = frappe.get_doc("Tally DocType Mapping", mapping_name)
    if mapping.object_type not in {"Ledger", "Stock Item", "Unit"}:
        return None
    return render_master(doc, company=connection.tally_company_name, action="Create", tally_name=None)


def _upsert_master_identity(q, success: bool):
    if not success:
        return

    source_doctype = q.source_doctype
    if source_doctype not in {"Customer", "Supplier", "Item", "UOM", "Account"}:
        return

    try:
        doc = frappe.get_doc(source_doctype, q.source_name)
    except Exception:
        return

    connection = frappe.get_doc("Tally Connection Settings", q.connection)
    display_name = _master_display_name(doc)
    existing = _master_identity(q.connection, source_doctype, q.source_name)
    source_hash = q.payload_hash
    remote_id = stable_remote_id(frappe.local.site, source_doctype, q.source_name)

    if existing:
        identity = frappe.get_doc("Tally Master Identity", existing["name"])
        identity.tally_name = display_name
        identity.stable_remote_id = remote_id
        identity.last_source_hash = source_hash
        identity.last_synced_at = now_datetime()
        identity.status = "Synced"
        identity.save(ignore_permissions=True)
    else:
        frappe.get_doc({
            "doctype": "Tally Master Identity",
            "connection": connection.name,
            "source_doctype": source_doctype,
            "source_name": q.source_name,
            "object_type": _master_object_type(source_doctype),
            "tally_name": display_name,
            "stable_remote_id": remote_id,
            "last_source_hash": source_hash,
            "last_synced_at": now_datetime(),
            "status": "Synced",
        }).insert(ignore_permissions=True)


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
    _upsert_master_identity(q, success)

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
