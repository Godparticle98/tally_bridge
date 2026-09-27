from __future__ import annotations

from datetime import datetime

from xml.etree.ElementTree import Element, SubElement, tostring

import frappe

from .utils import stable_remote_id


def _text(parent, tag, value):
    node = SubElement(parent, tag)
    node.text = "" if value is None else str(value)
    return node


def _envelope(messages, company: str, request_id: str) -> str:
    root = Element("ENVELOPE")
    h = SubElement(root, "HEADER")
    _text(h, "VERSION", "1")
    _text(h, "TALLYREQUEST", "Import Data")
    body = SubElement(root, "BODY")
    imp = SubElement(body, "IMPORTDATA")
    rd = SubElement(imp, "REQUESTDESC")
    _text(rd, "REPORTNAME", "All Masters")
    static = SubElement(rd, "STATICVARIABLES")
    _text(static, "SVCURRENTCOMPANY", company)
    _text(rd, "REQUESTID", request_id)
    req = SubElement(imp, "REQUESTDATA")
    for m in messages:
        req.append(m)
    return tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _effective_date(doc) -> str:
    value = getattr(doc, "modified", None) or getattr(doc, "creation", None)
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    if value:
        return str(value).replace("-", "")[:8]
    return datetime.now().strftime("%Y%m%d")


def _primary_address(doc):
    address_name = (
        getattr(doc, "customer_primary_address", None)
        or getattr(doc, "supplier_primary_address", None)
        or getattr(doc, "primary_address", None)
    )
    if not address_name:
        return None
    try:
        return frappe.get_doc("Address", address_name)
    except Exception:
        return None


def _address_lines(address):
    if not address:
        return []
    lines = []
    for field in ("address_line1", "address_line2"):
        value = getattr(address, field, None)
        if value:
            lines.append(str(value).strip())
    return [line for line in lines if line]


def _gst_registration_type(doc):
    value = (getattr(doc, "gst_category", None) or "").strip()
    mapping = {
        "Registered Regular": "Regular",
        "Registered Composition": "Composition",
        "Unregistered": "Unregistered/Consumer",
        "Consumer": "Unregistered/Consumer",
    }
    return mapping.get(value)


def _add_party_details(ledger, doc, display_name: str):
    address = _primary_address(doc)
    pan = getattr(doc, "pan", None)
    gstin = getattr(doc, "gstin", None) or getattr(doc, "tax_id", None)
    state = getattr(address, "gst_state", None) or getattr(address, "state", None) if address else None
    country = getattr(address, "country", None) if address else None
    pincode = getattr(address, "pincode", None) if address else None

    mail = SubElement(ledger, "LEDMAILINGDETAILS.LIST")
    _text(mail, "APPLICABLEFROM", _effective_date(doc))
    _text(mail, "MAILINGNAME", display_name)
    if state:
        _text(mail, "STATE", state)
    if country:
        _text(mail, "COUNTRY", country)
    if pincode:
        _text(mail, "PINCODE", pincode)

    lines = _address_lines(address)
    if lines:
        addr_list = SubElement(mail, "ADDRESS.LIST", {"TYPE": "String"})
        for line in lines:
            _text(addr_list, "ADDRESS", line)

    if pan:
        _text(ledger, "INCOMETAXNUMBER", pan)

    if gstin:
        _text(ledger, "PARTYGSTIN", gstin)

    gst_registration_type = _gst_registration_type(doc)
    if gst_registration_type or gstin or state:
        gst = SubElement(ledger, "LEDGSTREGDETAILS.LIST")
        _text(gst, "APPLICABLEFROM", _effective_date(doc))
        if gst_registration_type:
            _text(gst, "GSTREGISTRATIONTYPE", gst_registration_type)
        if state:
            _text(gst, "STATE", state)
            _text(gst, "PLACEOFSUPPLY", state)
        if gstin:
            _text(gst, "GSTIN", gstin)


def customer(doc, company: str, action="Create") -> str:
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    ledger = SubElement(m, "LEDGER", {"Action": action})
    # Keep the ERPNext document name as the visible ledger name for now.
    # A separate master identity mapping will later allow friendly Tally names
    # while still supporting reliable Alter operations after ERPNext renames.
    _text(ledger, "NAME", doc.name)
    _text(ledger, "PARENT", "Sundry Debtors")
    _add_party_details(ledger, doc, getattr(doc, "customer_name", None) or doc.name)
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    return _envelope([m], company, remote)


def supplier(doc, company: str, action="Create") -> str:
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    ledger = SubElement(m, "LEDGER", {"Action": action})
    _text(ledger, "NAME", doc.name)
    _text(ledger, "PARENT", "Sundry Creditors")
    _add_party_details(ledger, doc, getattr(doc, "supplier_name", None) or doc.name)
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    return _envelope([m], company, remote)


def item(doc, company: str, action="Create") -> str:
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    item = SubElement(m, "STOCKITEM", {"Action": action})
    _text(item, "NAME", doc.item_name or doc.name)
    if getattr(doc, "stock_uom", None):
        _text(item, "BASEUNITS", doc.stock_uom)
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    aliases = SubElement(item, "NAME.LIST", {"TYPE": "String"})
    _text(aliases, "NAME", remote)
    return _envelope([m], company, remote)


def uom(doc, company: str, action="Create") -> str:
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    unit = SubElement(m, "UNIT", {"Action": action})
    _text(unit, "NAME", doc.name)
    _text(unit, "ISSIMPLEUNIT", "Yes")
    _text(unit, "ORIGINALNAME", doc.name)
    _text(unit, "DECIMALPLACES", "2")
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    return _envelope([m], company, remote)


def _account_parent(doc) -> str:
    # Conservative starter heuristic; production should expose a configurable account-group mapping.
    root = (getattr(doc, "root_type", "") or "").lower()
    acct = (getattr(doc, "account_type", "") or "").lower()
    if acct == "bank":
        return "Bank Accounts"
    if acct == "cash":
        return "Cash-in-Hand"
    if acct in {"receivable", "receivable account"}:
        return "Sundry Debtors"
    if acct in {"payable", "payable account"}:
        return "Sundry Creditors"
    if acct in {"tax", "tax or charge"}:
        return "Duties & Taxes"
    if root == "equity":
        return "Capital Account"
    if root == "income":
        return "Sales Accounts"
    if root == "expense":
        return "Direct Expenses"
    if root == "liability":
        return "Current Liabilities"
    if root == "asset":
        return "Current Assets"
    return "Current Assets"


def account(doc, company: str, action="Create") -> str:
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    ledger = SubElement(m, "LEDGER", {"Action": action})
    _text(ledger, "NAME", doc.account_name or doc.name)
    _text(ledger, "PARENT", _account_parent(doc))
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    return _envelope([m], company, remote)


def render_master(doc, company: str, action="Create") -> str:
    if doc.doctype == "Customer":
        return customer(doc, company, action)
    if doc.doctype == "Supplier":
        return supplier(doc, company, action)
    if doc.doctype == "Item":
        return item(doc, company, action)
    if doc.doctype == "UOM":
        return uom(doc, company, action)
    if doc.doctype == "Account":
        return account(doc, company, action)
    frappe.throw(f"No master renderer registered for {doc.doctype}")
