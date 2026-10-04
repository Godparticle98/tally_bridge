from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, tostring

import frappe

from .utils import stable_remote_id
from .masters import tally_uom_name


def _text(parent, tag, value):
    node = SubElement(parent, tag)
    node.text = "" if value is None else str(value)
    return node


def _amount(value: float) -> str:
    return f"{float(value):.2f}"


def _master_name(connection, doctype, source_name):
    if not source_name:
        return source_name
    if not connection:
        return source_name
    value = frappe.db.get_value(
        "Tally Master Identity",
        {
            "connection": connection,
            "source_doctype": doctype,
            "source_name": source_name,
            "status": "Synced",
        },
        "tally_name",
    )
    return value or source_name


def _uom_name(connection, source_uom):
    """Resolve an ERPNext UOM to its Tally Unit symbol for voucher quantities."""
    if not source_uom:
        return source_uom
    mapped = _master_name(connection, "UOM", source_uom)
    if mapped and mapped != source_uom:
        return mapped
    try:
        return tally_uom_name(frappe.get_doc("UOM", source_uom))
    except Exception:
        return source_uom


def envelope(messages, company: str, request_id: str, data_id: str = "Vouchers") -> str:
    root = Element("ENVELOPE")
    header = SubElement(root, "HEADER")
    _text(header, "VERSION", "1")
    _text(header, "TALLYREQUEST", "Import")
    _text(header, "TYPE", "Data")
    _text(header, "ID", data_id)

    body = SubElement(root, "BODY")
    desc = SubElement(body, "DESC")
    static = SubElement(desc, "STATICVARIABLES")
    _text(static, "SVCURRENTCOMPANY", company)
    _text(static, "SVEXPORTFORMAT", "XML")
    _text(desc, "REQUESTID", request_id)

    data = SubElement(body, "DATA")
    for message in messages:
        data.append(message)

    return tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _ledger_entry(parent, ledger_name, amount, deemed_positive, party=False, bill=None, connection=None, ledger_doctype="Account"):
    e = SubElement(parent, "LEDGERENTRIES.LIST")
    _text(e, "LEDGERNAME", _master_name(connection, ledger_doctype, ledger_name))
    _text(e, "ISDEEMEDPOSITIVE", "Yes" if deemed_positive else "No")
    if party:
        _text(e, "ISPARTYLEDGER", "Yes")
        _text(e, "ISLASTDEEMEDPOSITIVE", "Yes" if deemed_positive else "No")
    _text(e, "AMOUNT", _amount(amount))
    if bill:
        allocations = bill if isinstance(bill, list) else [{"name": bill, "amount": abs(float(amount)), "bill_type": "New Ref"}]
        for allocation in allocations:
            b = SubElement(e, "BILLALLOCATIONS.LIST")
            _text(b, "NAME", allocation["name"])
            _text(b, "BILLTYPE", allocation.get("bill_type", "New Ref"))
            _text(b, "AMOUNT", _amount(allocation.get("amount", abs(float(amount)))))
    return e


def sales_invoice(doc, company: str, action: str = "Create", connection: str | None = None) -> str:
    remote_id = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    voucher = Element(
        "TALLYMESSAGE"
    )
    attrs = {
        "REMOTEID": remote_id,
        "VCHTYPE": "Sales",
        "ACTION": action,
        "OBJVIEW": "Invoice Voucher View",
    }
    v = SubElement(voucher, "VOUCHER", attrs)
    _text(v, "DATE", doc.posting_date.strftime("%Y%m%d") if hasattr(doc.posting_date, "strftime") else str(doc.posting_date).replace("-", ""))
    _text(v, "VOUCHERTYPENAME", "Sales")
    _text(v, "VOUCHERNUMBER", doc.name)
    _text(v, "PERSISTEDVIEW", "Invoice Voucher View")
    _text(v, "ISINVOICE", "Yes")
    _text(v, "PARTYLEDGERNAME", _master_name(connection, "Customer", doc.customer))
    _text(v, "NARRATION", doc.remarks or f"ERPNext {doc.name}")

    # Party is Dr in a standard sales invoice.
    _ledger_entry(v, doc.customer, -float(doc.grand_total), True, party=True, bill=doc.name, connection=connection, ledger_doctype="Customer")

    # Item and accounting allocations.
    for item in doc.items:
        inv = SubElement(v, "ALLINVENTORYENTRIES.LIST")
        _text(inv, "STOCKITEMNAME", _master_name(connection, "Item", item.item_code))
        _text(inv, "ISDEEMEDPOSITIVE", "No")
        qty = float(item.qty or 0)
        rate = float(item.rate or 0)
        amount = float(item.amount or 0)
        tally_uom = _uom_name(connection, item.stock_uom or item.uom)
        _text(inv, "ACTUALQTY", f"{qty:g} {tally_uom or ''}".strip())
        _text(inv, "BILLEDQTY", f"{qty:g} {tally_uom or ''}".strip())
        _text(inv, "RATE", f"{rate:g}/{tally_uom or 'NOS'}")
        _text(inv, "AMOUNT", _amount(amount))
        alloc = SubElement(inv, "ACCOUNTINGALLOCATIONS.LIST")
        _text(alloc, "LEDGERNAME", _master_name(connection, "Account", item.income_account))
        _text(alloc, "ISDEEMEDPOSITIVE", "No")
        _text(alloc, "AMOUNT", _amount(amount))

    # Taxes/charges become separate credit ledger entries.
    for tax in getattr(doc, "taxes", []) or []:
        if not tax.account_head:
            continue
        amount = float(tax.tax_amount or 0)
        if not amount:
            continue
        _ledger_entry(v, tax.account_head, amount, False, party=False, connection=connection)

    return envelope([voucher], company=company, request_id=remote_id)


def purchase_invoice(doc, company: str, action: str = "Create", connection: str | None = None) -> str:
    remote_id = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    voucher = Element("TALLYMESSAGE")
    v = SubElement(
        voucher,
        "VOUCHER",
        {"REMOTEID": remote_id, "VCHTYPE": "Purchase", "ACTION": action, "OBJVIEW": "Invoice Voucher View"},
    )
    _text(v, "DATE", str(doc.posting_date).replace("-", ""))
    _text(v, "EFFECTIVEDATE", str(doc.posting_date).replace("-", ""))
    _text(v, "VOUCHERTYPENAME", "Purchase")
    _text(v, "VOUCHERNUMBER", doc.name)
    if getattr(doc, "bill_no", None):
        _text(v, "REFERENCE", doc.bill_no)
    if getattr(doc, "bill_date", None):
        _text(v, "REFERENCEDATE", str(doc.bill_date).replace("-", ""))
    _text(v, "PERSISTEDVIEW", "Invoice Voucher View")
    _text(v, "ISINVOICE", "Yes")
    _text(v, "PARTYLEDGERNAME", _master_name(connection, "Supplier", doc.supplier))
    _text(v, "NARRATION", doc.remarks or f"ERPNext {doc.name}")

    # Supplier is Cr in a standard purchase invoice.
    _ledger_entry(v, doc.supplier, float(doc.grand_total), False, party=True, bill=doc.name, connection=connection, ledger_doctype="Supplier")

    purchase_ledger = getattr(frappe.get_doc("Tally Connection Settings", connection), "default_purchase_ledger", None) if connection else None
    purchase_ledger = (purchase_ledger or "Purchase").strip()

    for item in doc.items:
        inv = SubElement(v, "ALLINVENTORYENTRIES.LIST")
        _text(inv, "STOCKITEMNAME", _master_name(connection, "Item", item.item_code))
        _text(inv, "ISDEEMEDPOSITIVE", "Yes")
        qty = float(item.qty or 0)
        rate = float(item.rate or 0)
        amount = float(item.amount or 0)
        tally_uom = _uom_name(connection, item.stock_uom or item.uom)
        _text(inv, "ACTUALQTY", f"{qty:g} {tally_uom or ''}".strip())
        _text(inv, "BILLEDQTY", f"{qty:g} {tally_uom or ''}".strip())
        _text(inv, "RATE", f"{rate:g}/{tally_uom or 'NOS'}")
        _text(inv, "AMOUNT", _amount(-amount))
        alloc = SubElement(inv, "ACCOUNTINGALLOCATIONS.LIST")
        _text(alloc, "LEDGERNAME", purchase_ledger)
        _text(alloc, "ISDEEMEDPOSITIVE", "Yes")
        _text(alloc, "AMOUNT", _amount(amount))

    for tax in getattr(doc, "taxes", []) or []:
        if not tax.account_head:
            continue
        amount = float(tax.tax_amount or 0)
        if not amount:
            continue
        _ledger_entry(v, tax.account_head, -amount, True, party=False, connection=connection)

    return envelope([voucher], company=company, request_id=remote_id)


def _payment_bill_allocations(doc):
    allocations = []
    for ref in getattr(doc, "references", []) or []:
        if not getattr(ref, "reference_name", None) or not float(getattr(ref, "allocated_amount", 0) or 0):
            continue
        allocations.append(
            {
                "name": ref.reference_name,
                "amount": abs(float(ref.allocated_amount)),
                "bill_type": "Agst Ref",
            }
        )
    return allocations


def payment_entry(doc, company: str, action: str = "Create", connection: str | None = None) -> str:
    remote_id = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    voucher_type = {"Receive": "Receipt", "Pay": "Payment", "Internal Transfer": "Contra"}.get(doc.payment_type, "Journal")
    voucher = Element("TALLYMESSAGE")
    v = SubElement(voucher, "VOUCHER", {"REMOTEID": remote_id, "VCHTYPE": voucher_type, "ACTION": action, "OBJVIEW": "Accounting Voucher View"})
    _text(v, "DATE", str(doc.posting_date).replace("-", ""))
    _text(v, "EFFECTIVEDATE", str(doc.posting_date).replace("-", ""))
    _text(v, "VOUCHERTYPENAME", voucher_type)
    _text(v, "VOUCHERNUMBER", doc.name)
    if getattr(doc, "reference_no", None):
        _text(v, "REFERENCE", doc.reference_no)
    if getattr(doc, "reference_date", None):
        _text(v, "REFERENCEDATE", str(doc.reference_date).replace("-", ""))
    _text(v, "PERSISTEDVIEW", "Accounting Voucher View")
    _text(v, "ISINVOICE", "No")
    _text(v, "NARRATION", doc.remarks or f"ERPNext {doc.name}")

    amount = float(doc.paid_amount or doc.received_amount or 0)
    party_type = getattr(doc, "party_type", None)
    party = getattr(doc, "party", None)
    party_doctype = party_type if party_type in {"Customer", "Supplier", "Employee"} else "Account"
    bill = _payment_bill_allocations(doc)

    if doc.payment_type == "Receive":
        # Receipt: Dr bank/cash, Cr customer.
        _ledger_entry(v, doc.paid_to, amount, True, party=False, connection=connection)
        if party:
            _ledger_entry(
                v,
                party,
                -amount,
                False,
                party=True,
                bill=bill,
                connection=connection,
                ledger_doctype=party_doctype,
            )
        else:
            _ledger_entry(v, doc.paid_from, -amount, False, party=False, connection=connection)
    elif doc.payment_type == "Pay":
        # Payment: Dr supplier, Cr bank/cash.
        if party:
            _ledger_entry(
                v,
                party,
                amount,
                True,
                party=True,
                bill=bill,
                connection=connection,
                ledger_doctype=party_doctype,
            )
        else:
            _ledger_entry(v, doc.paid_to, amount, True, party=False, connection=connection)
        _ledger_entry(v, doc.paid_from, -amount, False, party=False, connection=connection)
    else:
        # Contra: Dr destination, Cr source.
        _ledger_entry(v, doc.paid_to, amount, True, party=False, connection=connection)
        _ledger_entry(v, doc.paid_from, -amount, False, party=False, connection=connection)

    return envelope([voucher], company=company, request_id=remote_id)


def journal_entry(doc, company: str, action: str = "Create", connection: str | None = None) -> str:
    remote_id = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    voucher = Element("TALLYMESSAGE")
    v = SubElement(voucher, "VOUCHER", {"REMOTEID": remote_id, "VCHTYPE": "Journal", "ACTION": action, "OBJVIEW": "Accounting Voucher View"})
    _text(v, "DATE", str(doc.posting_date).replace("-", ""))
    _text(v, "VOUCHERTYPENAME", "Journal")
    _text(v, "VOUCHERNUMBER", doc.name)
    _text(v, "PERSISTEDVIEW", "Accounting Voucher View")
    _text(v, "ISINVOICE", "No")
    _text(v, "NARRATION", doc.user_remark or f"ERPNext {doc.name}")
    for row in doc.accounts:
        debit = float(row.debit or 0)
        credit = float(row.credit or 0)
        party_type = getattr(row, "party_type", None)
        party = getattr(row, "party", None)
        ledger_doctype = party_type if party_type in {"Customer", "Supplier", "Employee"} else "Account"
        ledger_name = party if party else row.account
        bill = None
        reference_name = getattr(row, "reference_name", None)
        reference_type = getattr(row, "reference_type", None)
        if reference_name and reference_type:
            bill = [{"name": reference_name, "amount": abs(debit or credit), "bill_type": "Agst Ref"}]
        if debit:
            _ledger_entry(v, ledger_name, debit, True, party=bool(party), bill=bill, connection=connection, ledger_doctype=ledger_doctype)
        elif credit:
            _ledger_entry(v, ledger_name, -credit, False, party=bool(party), bill=bill, connection=connection, ledger_doctype=ledger_doctype)
    return envelope([voucher], company=company, request_id=remote_id)


def render_document(doc, company: str, action: str = "Create", connection: str | None = None) -> str:
    if doc.doctype == "Sales Invoice":
        return sales_invoice(doc, company, action, connection)
    if doc.doctype == "Purchase Invoice":
        return purchase_invoice(doc, company, action, connection)
    if doc.doctype == "Payment Entry":
        return payment_entry(doc, company, action, connection)
    if doc.doctype == "Journal Entry":
        return journal_entry(doc, company, action, connection)
    frappe.throw(f"No renderer registered for {doc.doctype}")
