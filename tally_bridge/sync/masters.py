from __future__ import annotations

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


def customer(doc, company: str, action="Create") -> str:
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    ledger = SubElement(m, "LEDGER", {"Action": action})
    _text(ledger, "NAME", doc.name)
    _text(ledger, "PARENT", "Sundry Debtors")
    if getattr(doc, "customer_name", None):
        ml = SubElement(ledger, "MAILINGNAME.LIST", {"TYPE": "String"})
        _text(ml, "MAILINGNAME", doc.customer_name)
    if getattr(doc, "tax_id", None):
        _text(ledger, "PARTYGSTIN", doc.tax_id)
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    return _envelope([m], company, remote)


def supplier(doc, company: str, action="Create") -> str:
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    ledger = SubElement(m, "LEDGER", {"Action": action})
    _text(ledger, "NAME", doc.name)
    _text(ledger, "PARENT", "Sundry Creditors")
    if getattr(doc, "supplier_name", None):
        ml = SubElement(ledger, "MAILINGNAME.LIST", {"TYPE": "String"})
        _text(ml, "MAILINGNAME", doc.supplier_name)
    if getattr(doc, "tax_id", None):
        _text(ledger, "PARTYGSTIN", doc.tax_id)
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
