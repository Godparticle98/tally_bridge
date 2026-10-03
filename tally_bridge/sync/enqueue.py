from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from .utils import canonical_json, sha256_text


MASTER_DOCTYPES = {"Customer", "Supplier", "Item", "UOM", "Account"}
TRANSACTION_DOCTYPES = {"Sales Invoice", "Purchase Invoice", "Payment Entry", "Journal Entry"}


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


def _master_dependencies(doc):
    deps = set()

    def add(doctype, name):
        if name:
            deps.add((doctype, str(name)))

    if doc.doctype == "Sales Invoice":
        add("Customer", doc.customer)
        for row in doc.items:
            add("Item", row.item_code)
            add("UOM", row.stock_uom or row.uom)
            add("Account", row.income_account)
        for row in doc.taxes:
            add("Account", row.account_head)

    elif doc.doctype == "Purchase Invoice":
        add("Supplier", doc.supplier)
        for row in doc.items:
            add("Item", row.item_code)
            add("UOM", row.stock_uom or row.uom)
            add("Account", row.expense_account)
        for row in doc.taxes:
            add("Account", row.account_head)

    elif doc.doctype == "Payment Entry":
        add("Account", doc.paid_from)
        add("Account", doc.paid_to)

    elif doc.doctype == "Journal Entry":
        for row in doc.accounts:
            add("Account", row.account)

    return sorted(deps)


def _enqueue_snapshot(connection, doc, event, priority):
    mapping = _find_mapping(doc.doctype, event)
    if not mapping:
        return None

    snapshot = doc.as_dict(no_nulls=False)
    payload = canonical_json(snapshot)
    payload_hash = sha256_text(payload)
    source_modified = getattr(doc, "modified", None)

    existing = frappe.db.exists(
        "Tally Sync Queue",
        {
            "source_doctype": doc.doctype,
            "source_name": doc.name,
            "event": event,
            "source_modified": source_modified,
            "connection": connection.name,
        },
    )
    if existing:
        return existing

    q = frappe.get_doc(
        {
            "doctype": "Tally Sync Queue",
            "connection": connection.name,
            "source_doctype": doc.doctype,
            "source_name": doc.name,
            "event": event,
            "source_modified": source_modified,
            "payload_json": payload,
            "payload_hash": payload_hash,
            "status": "Queued",
            "priority": priority,
            "attempts": 0,
            "queued_at": now_datetime(),
        }
    )
    q.insert(ignore_permissions=True)
    return q.name


def on_document_event(doc, method=None):
    """Snapshot and enqueue; never call Tally synchronously from an ERPNext request."""
    event = method or "on_update"
    mapping = _find_mapping(doc.doctype, event)
    if not mapping:
        return

    for connection in _enabled_companies():
        if getattr(doc, "company", None) and doc.company != connection.erpnext_company:
            continue

        # Transactions are deliberately lower priority than their master
        # dependencies. This follows Tally's requirement that referenced
        # masters exist before voucher import.
        if doc.doctype in TRANSACTION_DOCTYPES:
            for dependency_doctype, dependency_name in _master_dependencies(doc):
                if not frappe.db.exists(dependency_doctype, dependency_name):
                    continue
                dependency = frappe.get_doc(dependency_doctype, dependency_name)
                _enqueue_snapshot(
                    connection,
                    dependency,
                    "on_update",
                    20,
                )

        _enqueue_snapshot(
            connection,
            doc,
            event,
            mapping.get("priority") or (100 if doc.doctype in TRANSACTION_DOCTYPES else 50),
        )

    return None
