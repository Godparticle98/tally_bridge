"""
tally_bridge/api/export.py

All @frappe.whitelist methods called from the Tally Bridge UI and
per-doctype buttons (Sales Invoice, Purchase Invoice, etc.).
"""

import frappe
import json
import base64
from frappe.utils import now_datetime, today

from tally_bridge.utils.xml_generator import (
    generate_chart_of_accounts_xml,
    generate_parties_xml,
    generate_sales_invoice_xml,
    generate_purchase_invoice_xml,
    generate_payment_entry_xml,
    generate_journal_entry_xml,
    generate_bank_transaction_xml,
    generate_full_export_xml,
)
from tally_bridge.utils.excel_exporter import generate_excel_export
from tally_bridge.utils.tally_connector import push_to_tally, TallyConnector


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _create_log(export_type, export_format, from_date=None, to_date=None):
    log = frappe.new_doc("Tally Export Log")
    log.export_type = export_type
    log.export_format = export_format
    log.from_date = from_date
    log.to_date = to_date
    log.status = "Pending"
    log.exported_at = now_datetime()
    log.insert(ignore_permissions=True)
    frappe.db.commit()
    return log.name


def _finalize_log(log_name, records_exported, records_failed, xml_preview=None):
    log = frappe.get_doc("Tally Export Log", log_name)
    log.records_exported = records_exported
    log.records_failed = records_failed
    if xml_preview:
        log.xml_preview = xml_preview[:4000]
    if log.status == "Pending":
        log.status = "Success" if records_failed == 0 else "Partial"
    log.save(ignore_permissions=True)
    frappe.db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Connection test
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def test_tally_connection():
    """Test connectivity to Tally Prime XML server."""
    try:
        conn = TallyConnector()
        success, response = conn.test_connection()
        return {"success": success, "response": response[:500] if response else ""}
    except Exception as e:
        return {"success": False, "response": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Individual doctype exports
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def export_chart_of_accounts(push_to_tally_flag=False, company=None):
    company = company or frappe.defaults.get_user_default("Company")
    log_name = _create_log("Chart of Accounts", "Tally XML")
    try:
        xml_str, count = generate_chart_of_accounts_xml(company=company)
        _finalize_log(log_name, count, 0, xml_str)

        if frappe.parse_json(push_to_tally_flag):
            result = push_to_tally(xml_str, log_name)
            return {"success": True, "records": count, "log": log_name,
                    "tally": result, "xml": xml_str}

        return {"success": True, "records": count, "log": log_name, "xml": xml_str}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tally Bridge: Chart of Accounts export")
        return {"success": False, "error": str(e), "log": log_name}


@frappe.whitelist()
def export_parties(push_to_tally_flag=False, company=None):
    company = company or frappe.defaults.get_user_default("Company")
    log_name = _create_log("Parties", "Tally XML")
    try:
        xml_str, count = generate_parties_xml(company=company)
        _finalize_log(log_name, count, 0, xml_str)

        if frappe.parse_json(push_to_tally_flag):
            result = push_to_tally(xml_str, log_name)
            return {"success": True, "records": count, "log": log_name,
                    "tally": result, "xml": xml_str}

        return {"success": True, "records": count, "log": log_name, "xml": xml_str}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tally Bridge: Parties export")
        return {"success": False, "error": str(e), "log": log_name}


@frappe.whitelist()
def export_sales_invoices(from_date=None, to_date=None, push_to_tally_flag=False, company=None):
    company = company or frappe.defaults.get_user_default("Company")
    log_name = _create_log("Sales Invoice", "Tally XML", from_date, to_date)
    try:
        xml_str, count = generate_sales_invoice_xml(from_date, to_date, company)
        _finalize_log(log_name, count, 0, xml_str)

        if frappe.parse_json(push_to_tally_flag):
            result = push_to_tally(xml_str, log_name)
            return {"success": True, "records": count, "log": log_name, "tally": result}

        return {"success": True, "records": count, "log": log_name, "xml": xml_str}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tally Bridge: Sales Invoice export")
        return {"success": False, "error": str(e), "log": log_name}


@frappe.whitelist()
def export_purchase_invoices(from_date=None, to_date=None, push_to_tally_flag=False, company=None):
    company = company or frappe.defaults.get_user_default("Company")
    log_name = _create_log("Purchase Invoice", "Tally XML", from_date, to_date)
    try:
        xml_str, count = generate_purchase_invoice_xml(from_date, to_date, company)
        _finalize_log(log_name, count, 0, xml_str)

        if frappe.parse_json(push_to_tally_flag):
            result = push_to_tally(xml_str, log_name)
            return {"success": True, "records": count, "log": log_name, "tally": result}

        return {"success": True, "records": count, "log": log_name, "xml": xml_str}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tally Bridge: Purchase Invoice export")
        return {"success": False, "error": str(e), "log": log_name}


@frappe.whitelist()
def export_payment_entries(from_date=None, to_date=None, push_to_tally_flag=False, company=None):
    company = company or frappe.defaults.get_user_default("Company")
    log_name = _create_log("Payment Entry", "Tally XML", from_date, to_date)
    try:
        xml_str, count = generate_payment_entry_xml(from_date, to_date, company)
        _finalize_log(log_name, count, 0, xml_str)

        if frappe.parse_json(push_to_tally_flag):
            result = push_to_tally(xml_str, log_name)
            return {"success": True, "records": count, "log": log_name, "tally": result}

        return {"success": True, "records": count, "log": log_name, "xml": xml_str}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tally Bridge: Payment Entry export")
        return {"success": False, "error": str(e), "log": log_name}


@frappe.whitelist()
def export_journal_entries(from_date=None, to_date=None, push_to_tally_flag=False, company=None):
    company = company or frappe.defaults.get_user_default("Company")
    log_name = _create_log("Journal Entry", "Tally XML", from_date, to_date)
    try:
        xml_str, count = generate_journal_entry_xml(from_date, to_date, company)
        _finalize_log(log_name, count, 0, xml_str)

        if frappe.parse_json(push_to_tally_flag):
            result = push_to_tally(xml_str, log_name)
            return {"success": True, "records": count, "log": log_name, "tally": result}

        return {"success": True, "records": count, "log": log_name, "xml": xml_str}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tally Bridge: Journal Entry export")
        return {"success": False, "error": str(e), "log": log_name}


@frappe.whitelist()
def export_bank_transactions(from_date=None, to_date=None, push_to_tally_flag=False, company=None):
    company = company or frappe.defaults.get_user_default("Company")
    log_name = _create_log("Bank Transaction", "Tally XML", from_date, to_date)
    try:
        xml_str, count = generate_bank_transaction_xml(from_date, to_date, company)
        _finalize_log(log_name, count, 0, xml_str)

        if frappe.parse_json(push_to_tally_flag):
            result = push_to_tally(xml_str, log_name)
            return {"success": True, "records": count, "log": log_name, "tally": result}

        return {"success": True, "records": count, "log": log_name, "xml": xml_str}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tally Bridge: Bank Transaction export")
        return {"success": False, "error": str(e), "log": log_name}


# ─────────────────────────────────────────────────────────────────────────────
# Full / bulk export
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def export_all(from_date=None, to_date=None, export_format="Tally XML",
               push_to_tally_flag=False, company=None):
    """Export all enabled doctypes in one call."""
    company = company or frappe.defaults.get_user_default("Company")
    log_name = _create_log("Full Export", export_format, from_date, to_date)

    try:
        if export_format == "Excel":
            data = generate_excel_export(from_date, to_date, company)
            # Save file
            filename = f"tally_export_{today()}.xlsx"
            file_doc = frappe.get_doc({
                "doctype": "File",
                "file_name": filename,
                "content": data,
                "is_private": 1,
            })
            file_doc.insert(ignore_permissions=True)
            _finalize_log(log_name, 1, 0)
            log = frappe.get_doc("Tally Export Log", log_name)
            log.file_attachment = file_doc.file_url
            log.save(ignore_permissions=True)
            frappe.db.commit()
            return {"success": True, "log": log_name,
                    "file_url": file_doc.file_url, "format": "Excel"}

        # Tally XML (default)
        xml_str, count = generate_full_export_xml(from_date, to_date, company)
        _finalize_log(log_name, count, 0, xml_str)

        if frappe.parse_json(push_to_tally_flag):
            result = push_to_tally(xml_str, log_name)
            return {"success": True, "records": count, "log": log_name,
                    "tally": result, "format": "Tally XML"}

        return {"success": True, "records": count, "log": log_name,
                "xml": xml_str, "format": "Tally XML"}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tally Bridge: Full export")
        log = frappe.get_doc("Tally Export Log", log_name)
        log.status = "Failed"
        log.error_log = str(e)
        log.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": False, "error": str(e), "log": log_name}


# ─────────────────────────────────────────────────────────────────────────────
# Single-document export (called from doctype buttons)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def export_single_document(doctype, docname, push_to_tally_flag=False):
    """Export a single document to Tally XML."""
    doc = frappe.get_doc(doctype, docname)
    company = doc.company if hasattr(doc, "company") else frappe.defaults.get_user_default("Company")

    if doctype == "Sales Invoice":
        xml_str, count = generate_sales_invoice_xml(
            from_date=str(doc.posting_date), to_date=str(doc.posting_date), company=company
        )
    elif doctype == "Purchase Invoice":
        xml_str, count = generate_purchase_invoice_xml(
            from_date=str(doc.posting_date), to_date=str(doc.posting_date), company=company
        )
    elif doctype == "Payment Entry":
        xml_str, count = generate_payment_entry_xml(
            from_date=str(doc.posting_date), to_date=str(doc.posting_date), company=company
        )
    elif doctype == "Journal Entry":
        xml_str, count = generate_journal_entry_xml(
            from_date=str(doc.posting_date), to_date=str(doc.posting_date), company=company
        )
    else:
        frappe.throw(f"Doctype {doctype} is not supported for single export.")

    log_name = _create_log(doctype, "Tally XML", str(doc.posting_date), str(doc.posting_date))
    _finalize_log(log_name, count, 0, xml_str)

    if frappe.parse_json(push_to_tally_flag):
        result = push_to_tally(xml_str, log_name)
        return {"success": True, "records": count, "log": log_name, "tally": result}

    return {"success": True, "records": count, "log": log_name, "xml": xml_str}
