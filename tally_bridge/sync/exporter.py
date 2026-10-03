from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree as ET

import frappe
from frappe.utils import now_datetime

from .masters import render_master
from .renderer import render_document


def _messages_from_envelope(xml_text: str):
    root = ET.fromstring(xml_text)
    body = root.find("BODY")
    if body is None:
        return []
    data = body.find("DATA")
    if data is None:
        imp = body.find("IMPORTDATA")
        data = imp.find("REQUESTDATA") if imp is not None else None
    if data is None:
        return []
    return [node for node in list(data) if node.tag == "TALLYMESSAGE"]


def _build_envelope(messages, company: str, data_id: str):
    root = ET.Element("ENVELOPE")
    h = ET.SubElement(root, "HEADER")
    ET.SubElement(h, "VERSION").text = "1"
    ET.SubElement(h, "TALLYREQUEST").text = "Import"
    ET.SubElement(h, "TYPE").text = "Data"
    ET.SubElement(h, "ID").text = data_id
    b = ET.SubElement(root, "BODY")
    d = ET.SubElement(b, "DATA")
    for m in messages:
        d.append(m)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _query_docs(doctype, company, from_date, to_date):
    filters = {"posting_date": ["between", [from_date, to_date]]}
    if frappe.get_meta(doctype).has_field("company") and company:
        filters["company"] = company
    names = frappe.get_all(doctype, filters=filters, pluck="name", order_by="posting_date asc, name asc", limit_page_length=0)
    return [frappe.get_doc(doctype, n) for n in names]


def generate_period_export(job_name):
    job = frappe.get_doc("Tally Export Job", job_name)
    connection = frappe.get_doc("Tally Connection Settings", job.connection)
    job.status = "Processing"
    job.started_at = now_datetime()
    job.save(ignore_permissions=True)
    frappe.db.commit()

    try:
        tx_types = [x.strip() for x in (job.transaction_types or "").split(",") if x.strip()]
        docs = []
        for dt in tx_types:
            if not frappe.db.exists("DocType", dt):
                continue
            docs.extend(_query_docs(dt, connection.erpnext_company, job.from_date, job.to_date))

        transaction_messages = []
        referenced = {"Customer": set(), "Supplier": set(), "Item": set(), "UOM": set(), "Account": set()}
        for doc in docs:
            action = "Create"
            xml = render_document(doc, connection.tally_company_name, action, connection=connection.name)
            transaction_messages.extend(_messages_from_envelope(xml))
            if doc.doctype == "Sales Invoice":
                referenced["Customer"].add(doc.customer)
                for row in doc.items:
                    referenced["Item"].add(row.item_code)
                    referenced["UOM"].add(row.stock_uom or row.uom)
                    referenced["Account"].add(row.income_account)
                for row in doc.taxes:
                    referenced["Account"].add(row.account_head)
            elif doc.doctype == "Purchase Invoice":
                referenced["Supplier"].add(doc.supplier)
                for row in doc.items:
                    referenced["Item"].add(row.item_code)
                    referenced["UOM"].add(row.stock_uom or row.uom)
                    referenced["Account"].add(row.expense_account)
                for row in doc.taxes:
                    referenced["Account"].add(row.account_head)
            elif doc.doctype == "Payment Entry":
                referenced["Account"].update([doc.paid_from, doc.paid_to])
            elif doc.doctype == "Journal Entry":
                referenced["Account"].update([x.account for x in doc.accounts])

        master_messages = []
        if job.include_masters:
            if job.master_scope == "All Masters":
                master_sets = {
                    dt: set(frappe.get_all(dt, pluck="name", limit_page_length=0))
                    for dt in referenced
                    if frappe.db.exists("DocType", dt)
                }
            else:
                master_sets = referenced
            for dt, names in master_sets.items():
                for name in sorted(x for x in names if x):
                    if not frappe.db.exists(dt, name):
                        continue
                    master_messages.extend(_messages_from_envelope(render_master(frappe.get_doc(dt, name), connection.tally_company_name)))

        masters_xml = _build_envelope(master_messages, connection.tally_company_name, "All Masters")
        transactions_xml = _build_envelope(transaction_messages, connection.tally_company_name, "Vouchers")

        manifest = {
            "generated_at": now_datetime().isoformat(),
            "erpnext_company": connection.erpnext_company,
            "tally_company": connection.tally_company_name,
            "from_date": str(job.from_date),
            "to_date": str(job.to_date),
            "transaction_documents": len(docs),
            "master_records": len(master_messages),
            "format": job.format,
            "import_order": ["01_masters.xml", "02_transactions.xml"],
        }
        readme = (
            "Tally Bridge export\n\n"
            "1. Back up the Tally company.\n"
            "2. Import 01_masters.xml using Alt+O > Import > Masters.\n"
            "3. Import 02_transactions.xml using Alt+O > Import > Transactions.\n"
            "4. Review Exceptions and verify Day Book / Sales Register / Purchase Register.\n"
        )

        filename = f"TallyExport_{connection.erpnext_company}_{job.from_date}_{job.to_date}.zip".replace("/", "-")
        path = Path(frappe.get_site_path("private", "files", filename))
        path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(path, "w", ZIP_DEFLATED) as z:
            z.writestr("00_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
            z.writestr("01_masters.xml", masters_xml)
            z.writestr("02_transactions.xml", transactions_xml)
            z.writestr("README_IMPORT.txt", readme)

        job.output_file = f"/private/files/{filename}"
        job.status = "Completed"
        job.completed_at = now_datetime()
        job.error_message = None
        job.save(ignore_permissions=True)
        frappe.db.commit()
        return str(path)
    except Exception as exc:
        frappe.db.rollback()
        job.reload()
        job.status = "Failed"
        job.error_message = frappe.get_traceback()
        job.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.log_error(frappe.get_traceback(), "Tally Bridge Export")
        raise
