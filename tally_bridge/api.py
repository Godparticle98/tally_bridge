from __future__ import annotations

import frappe
from frappe import _

from .sync.service import (
    build_master_create_fallback,
    build_tally_payload,
    claim_next_job,
    complete_job,
    _master_identity,
)


_ALLOWED_ROLES = {"Tally Bridge Agent", "System Manager"}


def _check_agent():
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.PermissionError)
    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection(_ALLOWED_ROLES):
        frappe.throw(_("Tally Bridge Agent role required"), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def pull_next_job(agent_id: str):
    _check_agent()
    q = claim_next_job(agent_id)
    if not q:
        return {"job": None}
    payload = build_tally_payload(q)
    fallback_payload = None

    master_doctypes = {"Customer", "Supplier", "Item", "UOM", "Account"}
    if q.source_doctype in master_doctypes and q.event in {"on_update", "on_update_after_submit"}:
        if not _master_identity(q.connection, q.source_doctype, q.source_name):
            fallback_payload = build_master_create_fallback(q)

    return {
        "job": {
            "name": q.name,
            "connection": q.connection,
            "source_doctype": q.source_doctype,
            "source_name": q.source_name,
            "event": q.event,
            "payload_xml": payload,
            "fallback_payload_xml": fallback_payload,
            "attempts": q.attempts,
        }
    }


@frappe.whitelist(methods=["POST"])
def ack_job(queue_name: str, success: int, tally_response: str = "", http_status: int = 0, latency_ms: float = 0, error: str | None = None):
    _check_agent()
    complete_job(queue_name, bool(int(success)), tally_response, int(http_status), float(latency_ms), error)
    return {"ok": True}

@frappe.whitelist(methods=["POST"])
def create_period_export(connection: str, from_date: str, to_date: str, include_masters: int = 1, master_scope: str = "Masters Referenced by Transactions", format: str = "XML", transaction_types: str = "Sales Invoice, Purchase Invoice, Payment Entry, Journal Entry"):
    _check_agent()
    job = frappe.get_doc({
        "doctype": "Tally Export Job",
        "status": "Queued",
        "connection": connection,
        "from_date": from_date,
        "to_date": to_date,
        "include_masters": int(include_masters),
        "master_scope": master_scope,
        "format": format,
        "transaction_types": transaction_types,
    }).insert(ignore_permissions=True)
    frappe.enqueue("tally_bridge.sync.exporter.generate_period_export", job_name=job.name, queue="long", enqueue_after_commit=True)
    return {"job": job.name}
