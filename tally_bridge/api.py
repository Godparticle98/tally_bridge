from __future__ import annotations

import frappe
from frappe import _

from .sync.utils import stable_remote_id
from .sync.masters import tally_uom_name

from .sync.service import (
    build_master_create_fallback,
    build_tally_payload,
    claim_next_job,
    complete_job,
    _master_identity,
)
from .sync.enqueue import _enqueue_snapshot


_ALLOWED_ROLES = {"Tally Bridge Agent", "System Manager"}


_MASTER_OBJECT_TYPES = {
    "Customer": "Ledger",
    "Supplier": "Ledger",
    "Account": "Ledger",
    "Item": "Stock Item",
    "UOM": "Unit",
}

_MASTER_DISPLAY_FIELDS = {
    "Customer": "customer_name",
    "Supplier": "supplier_name",
    "Account": "account_name",
    "Item": "item_name",
    "UOM": "uom_name",
}


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
    master_probe = None

    if q.source_doctype in _MASTER_OBJECT_TYPES and q.event in {"on_update", "on_update_after_submit"}:
        if not _master_identity(q.connection, q.source_doctype, q.source_name):
            fallback_payload = build_master_create_fallback(q)
            connection = frappe.get_doc("Tally Connection Settings", q.connection)
            display_field = _MASTER_DISPLAY_FIELDS[q.source_doctype]
            tally_master_name = frappe.db.get_value(
                q.source_doctype,
                q.source_name,
                display_field,
            ) or q.source_name
            master_probe = {
                "name": tally_master_name,
                "object_type": _MASTER_OBJECT_TYPES[q.source_doctype],
                "company": connection.tally_company_name,
            }

    return {
        "job": {
            "name": q.name,
            "connection": q.connection,
            "source_doctype": q.source_doctype,
            "source_name": q.source_name,
            "event": q.event,
            "payload_xml": payload,
            "fallback_payload_xml": fallback_payload,
            "master_probe": master_probe,
            "attempts": q.attempts,
        }
    }


@frappe.whitelist(methods=["POST"])
def ack_job(
    queue_name: str,
    success: int,
    tally_response: str = "",
    http_status: int = 0,
    latency_ms: float = 0,
    error: str | None = None,
):
    _check_agent()
    complete_job(
        queue_name,
        bool(int(success)),
        tally_response,
        int(http_status),
        float(latency_ms),
        error,
    )
    return {"ok": True}




@frappe.whitelist(methods=["POST"])
def queue_export_job(job_name: str):
    _check_agent()
    job = frappe.get_doc("Tally Export Job", job_name)
    job.check_permission("write")
    if job.status == "Processing":
        frappe.throw(_("This export is already processing."))
    job.run_method("validate")
    job.status = "Queued"
    job.started_at = None
    job.completed_at = None
    job.error_message = None
    job.output_file = None
    job.save(ignore_permissions=True)
    frappe.db.commit()
    frappe.enqueue(
        "tally_bridge.sync.exporter.generate_period_export",
        job_name=job.name,
        queue="long",
        enqueue_after_commit=True,
    )
    return {"job": job.name, "status": "Queued"}

@frappe.whitelist(methods=["POST"])
def create_period_export(
    connection: str,
    from_date: str,
    to_date: str,
    include_masters: int = 1,
    master_scope: str = "Masters Referenced by Transactions",
    format: str = "XML",
    transaction_types: str = "Sales Invoice, Purchase Invoice, Payment Entry, Journal Entry",
):
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
    frappe.enqueue(
        "tally_bridge.sync.exporter.generate_period_export",
        job_name=job.name,
        queue="long",
        enqueue_after_commit=True,
    )
    return {"job": job.name}


@frappe.whitelist(methods=["POST"])
def reconcile_masters(connection: str):
    """Create a reconciliation job; the Windows Agent performs the Tally pull."""
    _check_agent()
    job = frappe.get_doc({
        "doctype": "Tally Reconciliation Job",
        "connection": connection,
        "status": "Queued",
        "requested_at": frappe.utils.now_datetime(),
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"job": job.name}


@frappe.whitelist(methods=["POST"])
def pull_reconciliation(agent_id: str):
    _check_agent()

    row = frappe.db.sql(
        """
        SELECT name, connection
        FROM `tabTally Reconciliation Job`
        WHERE status='Queued'
        ORDER BY requested_at ASC
        LIMIT 1
        """,
        as_dict=True,
    )
    if not row:
        return {"job": None}

    job = frappe.get_doc("Tally Reconciliation Job", row[0].name)
    job.status = "Processing"
    job.save(ignore_permissions=True)
    frappe.db.commit()

    connection = frappe.get_doc("Tally Connection Settings", job.connection)
    masters = []

    for doctype, name_field in _MASTER_DISPLAY_FIELDS.items():
        filters = {}
        if doctype == "Account":
            filters["is_group"] = 0

        rows = frappe.get_all(
            doctype,
            filters=filters,
            fields=["name", name_field],
            limit_page_length=0,
        )

        for master in rows:
            masters.append({
                "source_doctype": doctype,
                "source_name": master.name,
                "display_name": master.get(name_field) or master.name,
                "reconciliation_name": (
                    tally_uom_name(frappe.get_doc(doctype, master.name))
                    if doctype == "UOM"
                    else master.get(name_field) or master.name
                ),
                "object_type": _MASTER_OBJECT_TYPES[doctype],
            })

    return {
        "job": {
            "name": job.name,
            "connection": job.connection,
            "company": connection.tally_company_name,
            "erp_masters": masters,
        }
    }


@frappe.whitelist(methods=["POST"])
def ack_reconciliation(
    job_name: str,
    success: int,
    summary_json: str = "{}",
    matches_json: str = "[]",
    unmatched_json: str = "[]",
    error: str = "",
):
    _check_agent()

    job = frappe.get_doc("Tally Reconciliation Job", job_name)
    job.status = "Completed" if int(success) else "Failed"
    job.summary_json = summary_json
    job.matches_json = matches_json
    job.unmatched_json = unmatched_json
    job.error = error or None

    if int(success):
        try:
            matches = frappe.parse_json(matches_json or "[]")
            for match in matches:
                identity_name = frappe.db.exists(
                    "Tally Master Identity",
                    {
                        "connection": job.connection,
                        "source_doctype": match["source_doctype"],
                        "source_name": match["source_name"],
                    },
                )

                values = {
                    "connection": job.connection,
                    "source_doctype": match["source_doctype"],
                    "source_name": match["source_name"],
                    "object_type": match["object_type"],
                    "tally_name": match["tally_name"],
                    "stable_remote_id": stable_remote_id(
                        frappe.local.site,
                        match["source_doctype"],
                        match["source_name"],
                    ),
                    "status": "Synced",
                    "match_status": match.get("reconciliation_status") or "Matched",
                    "last_synced_at": frappe.utils.now_datetime(),
                }

                if identity_name:
                    identity = frappe.get_doc("Tally Master Identity", identity_name)
                    for field, value in values.items():
                        if field != "connection":
                            setattr(identity, field, value)
                    identity.save(ignore_permissions=True)
                else:
                    frappe.get_doc({
                        "doctype": "Tally Master Identity",
                        **values,
                    }).insert(ignore_permissions=True)
        except Exception as exc:
            job.error = f"Reconciliation identity-link error: {exc}"

    job.completed_at = frappe.utils.now_datetime()
    job.save(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist(methods=["POST"])
def provision_reconciled_masters(
    connection: str,
    source_doctypes: str = "UOM,Account,Customer,Supplier,Item",
    limit: int = 100,
    reconciliation_job: str | None = None,
):
    """Queue only reconciliation records explicitly classified as Create Required.

    Provisioning is intentionally separate from reconciliation so a large ERPNext
    dataset is never pushed into Tally accidentally. UOMs/accounts/parties are
    queued before Items; Item jobs depend on any newly queued UOM jobs.
    """
    _check_agent()

    limit = max(1, min(int(limit), 1000))
    allowed = {value.strip() for value in (source_doctypes or "").split(",") if value.strip()}
    invalid = allowed.difference(_MASTER_DISPLAY_FIELDS)
    if invalid:
        frappe.throw(_("Unsupported master doctypes: {0}").format(", ".join(sorted(invalid))))

    filters = {"connection": connection, "status": "Completed"}
    if reconciliation_job:
        filters["name"] = reconciliation_job

    job_rows = frappe.get_all(
        "Tally Reconciliation Job",
        filters=filters,
        fields=["name", "unmatched_json", "completed_at"],
        order_by="completed_at desc",
        limit_page_length=1,
    )
    if not job_rows:
        frappe.throw(_("No completed reconciliation job is available for this connection."))

    unmatched = frappe.parse_json(job_rows[0].unmatched_json or "[]")
    candidates = [
        row for row in unmatched
        if row.get("match_status") == "Create Required"
        and row.get("source_doctype") in allowed
    ][:limit]

    priority = {"UOM": 10, "Account": 20, "Customer": 20, "Supplier": 20, "Item": 30}

    created = []
    skipped = []
    connection_doc = frappe.get_doc("Tally Connection Settings", connection)
    for row in sorted(candidates, key=lambda x: (priority.get(x["source_doctype"], 50), x["source_name"])):
        doctype = row["source_doctype"]
        name = row["source_name"]

        # The reconciliation result is authoritative for this provisioning run.
        # If it says Create Required, any older identity may be stale (for example
        # a previous Agent version could have acknowledged HTTP 200 even when
        # Tally returned an import error). Do not let such an identity suppress
        # provisioning; remove the stale link and retry the master.
        identity = _master_identity(connection, doctype, name)
        if identity:
            try:
                frappe.delete_doc(
                    "Tally Master Identity",
                    identity["name"],
                    ignore_permissions=True,
                    force=True,
                )
            except Exception:
                pass

        try:
            doc = frappe.get_doc(doctype, name)
        except Exception:
            skipped.append({"source_doctype": doctype, "source_name": name, "reason": "ERPNext document no longer exists"})
            continue

        queue_name = _enqueue_snapshot(connection_doc, doc, "after_insert", priority.get(doctype, 50))

        # A previous failed/false-success queue row can also suppress a retry
        # because _enqueue_snapshot is idempotent. For an explicitly
        # Create-Required master, reset that row back to Queued.
        existing_queue = frappe.get_doc("Tally Sync Queue", queue_name) if queue_name else None
        if existing_queue and existing_queue.status in {"Success", "Failed", "Blocked"}:
            existing_queue.status = "Queued"
            existing_queue.attempts = 0
            existing_queue.next_attempt_at = None
            existing_queue.last_error = None
            existing_queue.last_tally_response = None
            existing_queue.last_http_status = None
            existing_queue.completed_at = None
            existing_queue.claimed_by = None
            existing_queue.claimed_at = None
            existing_queue.lease_until = None
            existing_queue.save(ignore_permissions=True)

        if queue_name:
            created.append({"queue": queue_name, "source_doctype": doctype, "source_name": name})
        else:
            skipped.append({"source_doctype": doctype, "source_name": name, "reason": "No active master mapping"})

    uom_jobs = {item["source_name"]: item["queue"] for item in created if item["source_doctype"] == "UOM"}
    if uom_jobs:
        for item in created:
            if item["source_doctype"] != "Item":
                continue
            doc = frappe.get_doc("Item", item["source_name"])
            uom_names = {getattr(doc, "stock_uom", None), getattr(doc, "default_unit_of_measure", None)}
            deps = [uom_jobs[name] for name in uom_names if name in uom_jobs]
            if deps:
                q = frappe.get_doc("Tally Sync Queue", item["queue"])
                current = frappe.parse_json(q.depends_on or "[]")
                q.depends_on = frappe.as_json(sorted(set(current + deps)))
                q.save(ignore_permissions=True)

    frappe.db.commit()
    return {
        "reconciliation_job": job_rows[0].name,
        "requested": len(candidates),
        "queued": len(created),
        "skipped": len(skipped),
        "queued_items": created,
        "skipped_items": skipped,
    }

@frappe.whitelist(methods=["GET", "POST"])
def get_dashboard(connection: str | None = None):
    """Return compact data for the Tally Bridge console."""
    _check_agent()

    if not connection:
        connection = frappe.db.get_value(
            "Tally Connection Settings",
            {"enabled": 1},
            "name",
            order_by="modified desc",
        )

    if not connection:
        return {"connection": None, "connections": [], "reconciliation": None, "queue": {}}

    connections = frappe.get_all(
        "Tally Connection Settings",
        fields=["name", "erpnext_company", "tally_company_name", "tally_url", "enabled"],
        order_by="modified desc",
        limit_page_length=100,
    )

    latest = frappe.get_all(
        "Tally Reconciliation Job",
        filters={"connection": connection},
        fields=["name", "status", "requested_at", "completed_at", "summary_json", "matches_json", "unmatched_json", "error"],
        order_by="requested_at desc",
        limit_page_length=1,
    )

    summary = {}
    if latest and latest[0].summary_json:
        try:
            summary = frappe.parse_json(latest[0].summary_json or "{}")
        except Exception:
            summary = {}

    queue_rows = frappe.db.sql(
        """
        SELECT status, COUNT(*) AS count
        FROM `tabTally Sync Queue`
        WHERE connection=%(connection)s
        GROUP BY status
        """,
        {"connection": connection},
        as_dict=True,
    )
    queue = {row.status: int(row.count) for row in queue_rows}

    identity_rows = frappe.db.sql(
        """
        SELECT object_type, status, COUNT(*) AS count
        FROM `tabTally Master Identity`
        WHERE connection=%(connection)s
        GROUP BY object_type, status
        """,
        {"connection": connection},
        as_dict=True,
    )
    identities = {}
    for row in identity_rows:
        identities.setdefault(row.object_type, {})[row.status] = int(row.count)

    return {
        "connection": connection,
        "connections": connections,
        "reconciliation": latest[0] if latest else None,
        "summary": summary,
        "queue": queue,
        "identities": identities,
    }
