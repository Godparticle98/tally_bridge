from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from .utils import canonical_json, sha256_text


def _find_mapping(doctype: str, event: str):
    rows = frappe.get_all(
        "Tally DocType Mapping",
        filters={"source_doctype": doctype, "enabled": 1, "trigger_event": event},
        fields=["name", "priority", "object_type", "tally_voucher_type"],
        order_by="priority asc, modified asc",
        limit_page_length=1,
    )
    return rows[0] if rows else None


def _enabled_companies():
    return frappe.get_all(
        "Tally Connection Settings",
        filters={"enabled": 1},
        fields=["name", "erpnext_company", "tally_company_name"],
        limit_page_length=100,
    )


def on_document_event(doc, method=None):
    """Document hook: snapshot and enqueue; never call Tally synchronously from an ERPNext request."""
    event = method or "on_update"
    mapping = _find_mapping(doc.doctype, event)
    if not mapping:
        return

    for connection in _enabled_companies():
        if getattr(doc, "company", None) and doc.company != connection.erpnext_company:
            continue

        snapshot = doc.as_dict(no_nulls=False)
        payload = canonical_json(snapshot)
        payload_hash = sha256_text(payload)
        existing = frappe.db.exists(
            "Tally Sync Queue",
            {
                "source_doctype": doc.doctype,
                "source_name": doc.name,
                "event": event,
                "source_modified": getattr(doc, "modified", None),
                "connection": connection.name,
            },
        )
        if existing:
            continue

        q = frappe.get_doc(
            {
                "doctype": "Tally Sync Queue",
                "connection": connection.name,
                "source_doctype": doc.doctype,
                "source_name": doc.name,
                "event": event,
                "source_modified": getattr(doc, "modified", None),
                "payload_json": payload,
                "payload_hash": payload_hash,
                "status": "Queued",
                "priority": mapping.get("priority") or 50,
                "attempts": 0,
                "queued_at": now_datetime(),
            }
        )
        q.insert(ignore_permissions=True)

    # Let the HTTP request finish; an agent will pull the queue separately.
    return None
