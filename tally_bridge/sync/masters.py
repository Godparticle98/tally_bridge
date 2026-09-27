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
    """Build the Tally HTTP-gateway import envelope used by the live agent."""
    root = Element("ENVELOPE")
    h = SubElement(root, "HEADER")
    _text(h, "VERSION", "1")
    _text(h, "TALLYREQUEST", "Import")
    _text(h, "TYPE", "Data")
    _text(h, "ID", "All Masters")

    body = SubElement(root, "BODY")
    desc = SubElement(body, "DESC")
    static = SubElement(desc, "STATICVARIABLES")
    _text(static, "SVCURRENTCOMPANY", company)

    data = SubElement(body, "DATA")
    for m in messages:
        data.append(m)
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
    """Render the party fields using the same hierarchy observed in a Tally export."""
    address = _primary_address(doc)
    pan = getattr(doc, "pan", None)
    gstin = getattr(doc, "gstin", None) or getattr(doc, "tax_id", None)

    state = None
    country = None
    pincode = None
    if address:
        state = getattr(address, "gst_state", None) or getattr(address, "state", None)
        country = getattr(address, "country", None)
        pincode = getattr(address, "pincode", None)

    if country:
        _text(ledger, "COUNTRYOFRESIDENCE", country)
    if pan:
        _text(ledger, "INCOMETAXNUMBER", pan)

    _text(ledger, "ISBILLWISEON", "Yes")

    if state:
        _text(ledger, "PRIORSTATENAME", state)

    gst_registration_type = _gst_registration_type(doc)
    if gst_registration_type or gstin or state:
        gst = SubElement(ledger, "LEDGSTREGDETAILS.LIST")
        _text(gst, "APPLICABLEFROM", _effective_date(doc))
        if gst_registration_type:
            _text(gst, "GSTREGISTRATIONTYPE", gst_registration_type)
        if state:
            _text(gst, "PLACEOFSUPPLY", state)
        if gstin:
            _text(gst, "GSTIN", gstin)

    mailing = SubElement(ledger, "LEDMAILINGDETAILS.LIST")
    lines = _address_lines(address)
    if lines:
        addr_list = SubElement(mailing, "ADDRESS.LIST", {"TYPE": "String"})
        for line in lines:
            _text(addr_list, "ADDRESS", line)
    _text(mailing, "APPLICABLEFROM", _effective_date(doc))
    if pincode:
        _text(mailing, "PINCODE", pincode)
    _text(mailing, "MAILINGNAME", display_name or "")
    if state:
        _text(mailing, "STATE", state)
    if country:
        _text(mailing, "COUNTRY", country)


def _party_ledger(doc, company: str, parent: str, display_name: str, action="Create") -> str:
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    ledger = SubElement(m, "LEDGER", {"NAME": doc.name, "ACTION": action.upper()})
    _text(ledger, "PARENT", parent)
    _add_party_details(ledger, doc, display_name)
    return _envelope([m], company, remote)


def customer(doc, company: str, action="Create") -> str:
    return _party_ledger(
        doc, company, "Sundry Debtors",
        getattr(doc, "customer_name", None) or doc.name,
        action,
    )


def supplier(doc, company: str, action="Create") -> str:
    return _party_ledger(
        doc, company, "Sundry Creditors",
        getattr(doc, "supplier_name", None) or doc.name,
        action,
    )


def item(doc, company: str, action="Create") -> str:
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    stock_item = SubElement(m, "STOCKITEM", {"NAME": doc.item_name or doc.name, "ACTION": action.upper()})
    if getattr(doc, "stock_uom", None):
        _text(stock_item, "BASEUNITS", doc.stock_uom)
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    aliases = SubElement(stock_item, "NAME.LIST", {"TYPE": "String"})
    _text(aliases, "NAME", remote)
    return _envelope([m], company, remote)


def uom(doc, company: str, action="Create") -> str:
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    unit = SubElement(m, "UNIT", {"NAME": doc.name, "Action": action})
    _text(unit, "ISSIMPLEUNIT", "Yes")
    _text(unit, "ORIGINALNAME", doc.name)
    _text(unit, "DECIMALPLACES", "2")
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    return _envelope([m], company, remote)


def _account_parent(doc) -> str:
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
    ledger = SubElement(m, "LEDGER", {"NAME": doc.account_name or doc.name, "ACTION": action.upper()})
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
