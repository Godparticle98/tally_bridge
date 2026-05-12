"""
tally_bridge/utils/xml_generator.py

Generates Tally Prime 4.x compatible XML (TALLYMESSAGE format) from ERPNext data.
Covers: Ledgers (Chart of Accounts + Parties), Sales, Purchase, Payment,
        Receipt, Journal vouchers, and Bank Transactions.
"""

import frappe
from frappe.utils import flt, cstr, formatdate
from lxml import etree


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _settings():
    return frappe.get_single("Tally Settings")


def _tally_date(dt):
    """Convert ERPNext date to Tally's YYYYMMDD format.
    Falls back to today if dt is None/empty to avoid 'voucher date missing' error.
    """
    import datetime
    if not dt:
        dt = frappe.utils.today()
    if isinstance(dt, str):
        dt = frappe.utils.getdate(dt)
    return dt.strftime("%Y%m%d")


def _get_applicable_from_date(from_date=None):
    """Determine the applicable from date based on user filter or financial year start."""
    if from_date:
        return _tally_date(from_date)
    today = frappe.utils.getdate(frappe.utils.today())
    fy_year = today.year if today.month >= 4 else today.year - 1
    return f"{fy_year}0401"


def _amount(val):
    """Format amount as string with 2 decimal places."""
    return f"{flt(val, 2):.2f}"


def _sub(parent, tag, text="", **attribs):
    """Create a subelement with optional text and attributes."""
    el = etree.SubElement(parent, tag, **attribs)
    if text:
        el.text = cstr(text)
    return el


def _envelope(company):
    """Create the outer ENVELOPE wrapper required by Tally."""
    root = etree.Element("ENVELOPE")
    header = etree.SubElement(root, "HEADER")
    _sub(header, "TALLYREQUEST", "Import Data")
    body = etree.SubElement(root, "BODY")
    importdata = etree.SubElement(body, "IMPORTDATA")
    requestdesc = etree.SubElement(importdata, "REQUESTDESC")
    _sub(requestdesc, "REPORTNAME", "All Masters")
    staticvariables = etree.SubElement(requestdesc, "STATICVARIABLES")
    _sub(staticvariables, "SVCURRENTCOMPANY", company)
    requestdata = etree.SubElement(importdata, "REQUESTDATA")
    tallymessage = etree.SubElement(requestdata, "TALLYMESSAGE",
                                    nsmap={"UDF": "TallyUDF"})
    return root, tallymessage


def _voucher_envelope(company, report_name="Vouchers"):
    """Create envelope for voucher imports."""
    root = etree.Element("ENVELOPE")
    header = etree.SubElement(root, "HEADER")
    _sub(header, "TALLYREQUEST", "Import Data")
    body = etree.SubElement(root, "BODY")
    importdata = etree.SubElement(body, "IMPORTDATA")
    requestdesc = etree.SubElement(importdata, "REQUESTDESC")
    _sub(requestdesc, "REPORTNAME", report_name)
    staticvariables = etree.SubElement(requestdesc, "STATICVARIABLES")
    _sub(staticvariables, "SVCURRENTCOMPANY", company)
    requestdata = etree.SubElement(importdata, "REQUESTDATA")
    tallymessage = etree.SubElement(requestdata, "TALLYMESSAGE",
                                    nsmap={"UDF": "TallyUDF"})
    return root, tallymessage


def _to_xml_string(root):
    return etree.tostring(root, pretty_print=True,
                          xml_declaration=True, encoding="UTF-8").decode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Chart of Accounts → Tally Ledgers
# ─────────────────────────────────────────────────────────────────────────────

# ERPNext root type → Tally group
_ACCOUNT_GROUP_MAP = {
    "Asset": "Current Assets",
    "Liability": "Current Liabilities",
    "Income": "Sales Accounts",
    "Expense": "Indirect Expenses",
    "Equity": "Capital Account",
}


def generate_chart_of_accounts_xml(from_date=None, to_date=None, company=None):
    """Export all GL Accounts as Tally ledgers."""
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _envelope(company)

    accounts = frappe.get_all(
        "Account",
        filters={"company": company, "is_group": 0},
        fields=["name", "account_name", "root_type", "parent_account",
                "account_currency", "account_type"]
    )

    for acc in accounts:
        grp = _ACCOUNT_GROUP_MAP.get(acc.root_type, "Indirect Expenses")
        ledger = etree.SubElement(
            tallymessage, "LEDGER",
            attrib={"NAME": acc.account_name, "ACTION": "Create"}
        )
        _sub(ledger, "NAME", acc.account_name)
        _sub(ledger, "PARENT", grp)
        _sub(ledger, "CURRENCYNAME", acc.account_currency or "INR")
        _sub(ledger, "ISBILLWISEON", "No")
        _sub(ledger, "AFFECTSSTOCK", "No")

    return _to_xml_string(root), len(accounts)


# ─────────────────────────────────────────────────────────────────────────────
# Customers & Suppliers → Tally Ledgers (Debtors / Creditors)
# ─────────────────────────────────────────────────────────────────────────────

def _get_party_address(party_type, party_name):
    """
    Fetch the primary address for a Customer or Supplier.
    Returns a dict with address fields (all may be empty strings if not found).
    """
    empty = {
        "address_line1": "", "address_line2": "", "city": "",
        "state": "", "country": "", "pincode": "", "gstin": "",
        "gst_category": ""
    }
    try:
        # Find the linked address via Dynamic Link
        addr_name = frappe.db.get_value(
            "Dynamic Link",
            {"link_doctype": party_type, "link_name": party_name,
             "parenttype": "Address"},
            "parent"
        )
        if not addr_name:
            return empty
        addr = frappe.get_cached_doc("Address", addr_name)
        return {
            "address_line1": cstr(addr.get("address_line1") or ""),
            "address_line2": cstr(addr.get("address_line2") or ""),
            "city":          cstr(addr.get("city") or ""),
            "state":         cstr(addr.get("state") or ""),
            "country":       cstr(addr.get("country") or ""),
            "pincode":       cstr(addr.get("pincode") or ""),
            "gstin":         cstr(addr.get("gstin") or ""),
            "gst_category":  cstr(addr.get("gst_category") or ""),
        }
    except Exception:
        return empty


def _build_address_lines(addr):
    """
    Return a list of non-empty address line strings for Tally's ADDRESS.LIST.
    Each element becomes a separate <ADDRESS> child in the list, which is how
    Tally Prime stores multi-line mailing addresses.
    """
    lines = []
    if addr.get("address_line1", "").strip():
        lines.append(addr["address_line1"].strip())
    if addr.get("address_line2", "").strip():
        lines.append(addr["address_line2"].strip())
    # City + Pincode on the last line, e.g. "Coimbatore - 641035"
    city = addr.get("city", "").strip()
    pin  = addr.get("pincode", "").strip()
    if city and pin:
        lines.append(f"{city} - {pin}")
    elif city:
        lines.append(city)
    elif pin:
        lines.append(pin)
    return lines


def _gst_reg_type(gst_category):
    """
    Map ERPNext GST Category to Tally GSTREGISTRATIONTYPE.
    """
    mapping = {
        "Registered Regular": "Regular",
        "Registered Composition": "Composition",
        "SEZ": "Regular-SEZ",
        "Overseas": "Overseas",
        "Consumer": "Consumer",
        "Deemed Export": "Deemed Exports - Refund",
        "UIN Holders": "UIN Holders",
        "Tax Deductor": "Tax Deductor",
        "Unregistered": "Unregistered",
    }
    return mapping.get(gst_category, "Regular") if gst_category else "Regular"


def _add_party_ledger(tallymessage, party_name, parent_group, addr, tax_id, applicable_from):
    ledger = etree.SubElement(
        tallymessage, "LEDGER",
        attrib={"NAME": party_name, "ACTION": "Create"}
    )
    
    # Basic Information
    _sub(ledger, "NAME", party_name)
    _sub(ledger, "PARENT", parent_group)
    
    # Old Audit Entries
    old_audit_list = etree.SubElement(ledger, "OLDAUDITENTRYIDS.LIST", TYPE="Number")
    _sub(old_audit_list, "OLDAUDITENTRYIDS", "-1")

    _sub(ledger, "CURRENCYNAME", "INR")
    
    # Regional and Tax classifications
    if addr["state"]:
        _sub(ledger, "PRIORSTATENAME", addr["state"])
    _sub(ledger, "VATDEALERTYPE", "Regular")
    _sub(ledger, "TAXCLASSIFICATIONNAME", "")
    _sub(ledger, "TAXTYPE", "Others")
    _sub(ledger, "BILLCREDITPERIOD", "30 Days")
    
    if addr["country"]:
        _sub(ledger, "COUNTRYOFRESIDENCE", addr["country"])
    _sub(ledger, "LEDGERCOUNTRYISDCODE", "+91")
    
    # Hardcoded defaults from Tally XML are removed to prevent empty lists and elements
    _sub(ledger, "ISBILLWISEON", "Yes")
    _sub(ledger, "ISTDSAPPLICABLE", "Yes")
    _sub(ledger, "ISCHEQUEPRINTINGENABLED", "Yes")
    _sub(ledger, "AFFECTSSTOCK", "No")

    # GST registration block
    gstin = addr["gstin"] or tax_id or ""
    if gstin:
        gst_type = _gst_reg_type(addr["gst_category"])
        gst_list = etree.SubElement(ledger, "LEDGSTREGDETAILS.LIST")
        _sub(gst_list, "APPLICABLEFROM", applicable_from)
        _sub(gst_list, "GSTREGISTRATIONTYPE", gst_type)
        if addr["state"]:
            _sub(gst_list, "PLACEOFSUPPLY", addr["state"])
        _sub(gst_list, "GSTIN", gstin)
        _sub(gst_list, "ISOTHTERRITORYASSESSEE", "No")
        _sub(gst_list, "CONSIDERPURCHASEFOREXPORT", "No")
        _sub(gst_list, "ISTRANSPORTER", "No")
        _sub(gst_list, "ISCOMMONPARTY", "No")
    elif tax_id:
        _sub(ledger, "INCOMETAXNUMBER", tax_id)

    # Mailing address block — multi-line ADDRESS.LIST
    addr_lines = _build_address_lines(addr)
    mail_list = etree.SubElement(ledger, "LEDMAILINGDETAILS.LIST")
    addr_list_el = etree.SubElement(mail_list, "ADDRESS.LIST", TYPE="String")
    for line in addr_lines:
        _sub(addr_list_el, "ADDRESS", line)
    _sub(mail_list, "APPLICABLEFROM", applicable_from)
    if addr["pincode"]:
        _sub(mail_list, "PINCODE", addr["pincode"])
    _sub(mail_list, "MAILINGNAME", party_name)
    if addr["state"]:
        _sub(mail_list, "STATE", addr["state"])
    if addr["country"]:
        _sub(mail_list, "COUNTRY", addr["country"])
        
    lang_list = etree.SubElement(ledger, "LANGUAGENAME.LIST")
    name_list = etree.SubElement(lang_list, "NAME.LIST", TYPE="String")
    _sub(name_list, "NAME", party_name)
    _sub(lang_list, "LANGUAGEID", "1033")

    deduct_rules = etree.SubElement(ledger, "DEDUCTINSAMEVCHRULES.LIST")
    _sub(deduct_rules, "NATUREOFPAYMENT", "")
    
    contact_details = etree.SubElement(ledger, "CONTACTDETAILS.LIST")
    _sub(contact_details, "NAME", "Primary Mobile No.")
    _sub(contact_details, "COUNTRYISDCODE", "+91")
    _sub(contact_details, "ISDEFAULTWHATSAPPNUM", "Yes")


def generate_parties_xml(from_date=None, to_date=None, company=None):
    """Export Customers and Suppliers as Tally ledgers under Sundry Debtors/Creditors.
    Includes mailing address, state, country, pincode, GSTIN and GST registration type.
    """
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _envelope(company)
    count = 0
    applicable_from = _get_applicable_from_date(from_date)

    customers = frappe.get_all(
        "Customer",
        fields=["name", "customer_name", "customer_group", "tax_id"]
    )
    for cust in customers:
        addr = _get_party_address("Customer", cust.name)
        _add_party_ledger(tallymessage, cust.customer_name, settings.sundry_debtors_ledger or "Sundry Debtors", addr, cust.tax_id, applicable_from)
        count += 1

    suppliers = frappe.get_all(
        "Supplier",
        fields=["name", "supplier_name", "supplier_group", "tax_id"]
    )
    for sup in suppliers:
        addr = _get_party_address("Supplier", sup.name)
        _add_party_ledger(tallymessage, sup.supplier_name, settings.sundry_creditors_ledger or "Sundry Creditors", addr, sup.tax_id, applicable_from)
        count += 1

    return _to_xml_string(root), count


# ─────────────────────────────────────────────────────────────────────────────
# Sales Invoice → Tally Sales Voucher
# ─────────────────────────────────────────────────────────────────────────────

def generate_sales_invoice_xml(from_date=None, to_date=None, company=None):
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _voucher_envelope(company, "Vouchers")

    filters = {"docstatus": 1, "company": company}
    if from_date and to_date:
        filters["posting_date"] = ["between", [from_date, to_date]]
    elif from_date:
        filters["posting_date"] = [">=", from_date]
    elif to_date:
        filters["posting_date"] = ["<=", to_date]

    invoices = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=["name", "customer", "customer_name", "posting_date",
                "grand_total", "base_grand_total", "net_total",
                "total_taxes_and_charges", "currency", "remarks"]
    )

    count = 0
    for inv in invoices:
        doc = frappe.get_doc("Sales Invoice", inv.name)
        tally_dt = _tally_date(inv.posting_date)
        voucher = etree.SubElement(
            tallymessage, "VOUCHER",
            attrib={"REMOTEID": inv.name, "VCHTYPE": settings.sales_voucher_type or "Sales",
                    "ACTION": "Create"}
        )
        _sub(voucher, "DATE", tally_dt)
        _sub(voucher, "EFFECTIVEDATE", tally_dt)
        _sub(voucher, "VOUCHERTYPENAME", settings.sales_voucher_type or "Sales")
        _sub(voucher, "VOUCHERNUMBER", inv.name)
        _sub(voucher, "PARTYLEDGERNAME", inv.customer_name)
        _sub(voucher, "PARTYNAME", inv.customer_name)
        _sub(voucher, "BASICBUYERNAME", inv.customer_name)
        _sub(voucher, "PARTYMAILINGNAME", inv.customer_name)
        _sub(voucher, "NARRATION", cstr(inv.remarks or f"Sales Invoice {inv.name}"))
        _sub(voucher, "ISINVOICE", "Yes")

        # Party details for Voucher
        addr = _get_party_address("Customer", inv.customer)
        if addr["country"]:
            _sub(voucher, "COUNTRYOFRESIDENCE", addr["country"])
        if addr["state"]:
            _sub(voucher, "STATENAME", addr["state"])
            _sub(voucher, "CONSIGNEESTATENAME", addr["state"])
            _sub(voucher, "PLACEOFSUPPLY", addr["state"])
        if addr["gstin"]:
            _sub(voucher, "PARTYGSTIN", addr["gstin"])
            _sub(voucher, "CONSIGNEEGSTIN", addr["gstin"])
            _sub(voucher, "PARTYTAXREGISTRATIONTYPE", _gst_reg_type(addr["gst_category"]))
            _sub(voucher, "CONSIGNEEGSTREGISTRATIONTYPE", _gst_reg_type(addr["gst_category"]))
        
        addr_lines = _build_address_lines(addr)
        if addr_lines:
            # Ship To (Consignee)
            buyer_addr_list = etree.SubElement(voucher, "BASICBUYERADDRESS.LIST", TYPE="String")
            for line in addr_lines:
                _sub(buyer_addr_list, "BASICBUYERADDRESS", line)
            # Bill To (Buyer)
            addr_list = etree.SubElement(voucher, "ADDRESS.LIST", TYPE="String")
            for line in addr_lines:
                _sub(addr_list, "ADDRESS", line)

        # Inventory entries (Items)
        for item in doc.items:
            inventory_entry = etree.SubElement(voucher, "ALLINVENTORYENTRIES.LIST")
            _sub(inventory_entry, "STOCKITEMNAME", item.item_name or item.item_code)
            _sub(inventory_entry, "ISDEEMEDPOSITIVE", "No")  # Outward / sales = No

            _sub(inventory_entry, "RATE", f"{_amount(item.rate)}/{item.uom}")
            # Sales: item amount is a CREDIT in the income account → positive value
            _sub(inventory_entry, "AMOUNT", _amount(item.base_net_amount))
            _sub(inventory_entry, "ACTUALQTY", f" {item.qty} {item.uom}")
            _sub(inventory_entry, "BILLEDQTY", f" {item.qty} {item.uom}")

            # Accounting Allocation for the Sales Ledger
            accounting_alloc = etree.SubElement(inventory_entry, "ACCOUNTINGALLOCATIONS.LIST")
            ledger_name = settings.sales_ledger or "Sales"
            _sub(accounting_alloc, "LEDGERNAME", ledger_name)
            _sub(accounting_alloc, "ISDEEMEDPOSITIVE", "No")   # Credit → No
            _sub(accounting_alloc, "AMOUNT", _amount(item.base_net_amount))  # Positive credit amount

        # Ledger entries section
        # Party entry (Debit)
        party_entry = etree.SubElement(voucher, "LEDGERENTRIES.LIST")
        _sub(party_entry, "LEDGERNAME", inv.customer_name)
        _sub(party_entry, "ISDEEMEDPOSITIVE", "Yes")
        _sub(party_entry, "AMOUNT", f"-{_amount(inv.base_grand_total)}")

        # Tax and Charge entries
        for tax in doc.taxes:
            if flt(tax.tax_amount) != 0:
                tax_entry = etree.SubElement(voucher, "LEDGERENTRIES.LIST")
                ledger_name = _strip_company(tax.account_head)
                if "TDS" in ledger_name.upper():
                    ledger_name = settings.tds_deductible_ledger or "TDS-Deductible"
                _sub(tax_entry, "LEDGERNAME", ledger_name)
                
                # In ERPNext: base_tax_amount_after_discount_amount holds the actual tax value added/deducted.
                # If negative, it's a deduction (Debit -> ISDEEMEDPOSITIVE=Yes, Amount=-ve)
                # If positive, it's an addition (Credit -> ISDEEMEDPOSITIVE=No, Amount=+ve)
                tax_val = flt(tax.base_tax_amount_after_discount_amount)
                if not tax_val:
                    tax_val = flt(tax.base_tax_amount)
                
                is_deduction = tax.get("add_deduct_tax") == "Deduct" or tax_val < 0 or "TDS" in ledger_name.upper()
                is_debit = is_deduction
                
                _sub(tax_entry, "ISDEEMEDPOSITIVE", "Yes" if is_debit else "No")
                _sub(tax_entry, "AMOUNT", f"-{_amount(abs(tax_val))}" if is_debit else _amount(abs(tax_val)))

        count += 1

    return _to_xml_string(root), count


# ─────────────────────────────────────────────────────────────────────────────
# Purchase Invoice → Tally Purchase Voucher
# ─────────────────────────────────────────────────────────────────────────────

def generate_purchase_invoice_xml(from_date=None, to_date=None, company=None):
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _voucher_envelope(company, "Vouchers")

    filters = {"docstatus": 1, "company": company}
    if from_date and to_date:
        filters["posting_date"] = ["between", [from_date, to_date]]
    elif from_date:
        filters["posting_date"] = [">=", from_date]
    elif to_date:
        filters["posting_date"] = ["<=", to_date]

    invoices = frappe.get_all(
        "Purchase Invoice",
        filters=filters,
        fields=["name", "supplier", "supplier_name", "posting_date",
                "grand_total", "base_grand_total", "net_total",
                "total_taxes_and_charges", "bill_no", "remarks"]
    )

    count = 0
    for inv in invoices:
        doc = frappe.get_doc("Purchase Invoice", inv.name)
        tally_dt = _tally_date(inv.posting_date)
        voucher = etree.SubElement(
            tallymessage, "VOUCHER",
            attrib={"REMOTEID": inv.name,
                    "VCHTYPE": settings.purchase_voucher_type or "Purchase",
                    "ACTION": "Create"}
        )
        _sub(voucher, "DATE", tally_dt)
        _sub(voucher, "EFFECTIVEDATE", tally_dt)
        _sub(voucher, "VOUCHERTYPENAME", settings.purchase_voucher_type or "Purchase")
        _sub(voucher, "VOUCHERNUMBER", inv.bill_no or inv.name)
        _sub(voucher, "PARTYLEDGERNAME", inv.supplier_name)
        _sub(voucher, "PARTYNAME", inv.supplier_name)
        _sub(voucher, "BASICBUYERNAME", inv.supplier_name)
        _sub(voucher, "PARTYMAILINGNAME", inv.supplier_name)
        _sub(voucher, "NARRATION", cstr(inv.remarks or f"Purchase Invoice {inv.name}"))
        _sub(voucher, "ISINVOICE", "Yes")

        # Party details for Voucher
        addr = _get_party_address("Supplier", inv.supplier)
        if addr["country"]:
            _sub(voucher, "COUNTRYOFRESIDENCE", addr["country"])
        if addr["state"]:
            _sub(voucher, "STATENAME", addr["state"])
            _sub(voucher, "CONSIGNEESTATENAME", addr["state"])
            _sub(voucher, "PLACEOFSUPPLY", addr["state"])
        if addr["gstin"]:
            _sub(voucher, "PARTYGSTIN", addr["gstin"])
            _sub(voucher, "CONSIGNEEGSTIN", addr["gstin"])
            _sub(voucher, "PARTYTAXREGISTRATIONTYPE", _gst_reg_type(addr["gst_category"]))
            _sub(voucher, "CONSIGNEEGSTREGISTRATIONTYPE", _gst_reg_type(addr["gst_category"]))
        
        addr_lines = _build_address_lines(addr)
        if addr_lines:
            # Ship To (Consignee)
            buyer_addr_list = etree.SubElement(voucher, "BASICBUYERADDRESS.LIST", TYPE="String")
            for line in addr_lines:
                _sub(buyer_addr_list, "BASICBUYERADDRESS", line)
            # Bill To (Buyer)
            addr_list = etree.SubElement(voucher, "ADDRESS.LIST", TYPE="String")
            for line in addr_lines:
                _sub(addr_list, "ADDRESS", line)

        # Inventory entries (Items)
        for item in doc.items:
            inventory_entry = etree.SubElement(voucher, "ALLINVENTORYENTRIES.LIST")
            _sub(inventory_entry, "STOCKITEMNAME", item.item_name or item.item_code)
            _sub(inventory_entry, "ISDEEMEDPOSITIVE", "Yes") # Inward is Yes
            
            _sub(inventory_entry, "RATE", f"{_amount(item.rate)}/{item.uom}")
            _sub(inventory_entry, "AMOUNT", f"-{_amount(item.base_net_amount)}") # Debit -> negative
            _sub(inventory_entry, "ACTUALQTY", f" {item.qty} {item.uom}")
            _sub(inventory_entry, "BILLEDQTY", f" {item.qty} {item.uom}")
            
            # Accounting Allocation for the Purchase Ledger
            accounting_alloc = etree.SubElement(inventory_entry, "ACCOUNTINGALLOCATIONS.LIST")
            ledger_name = settings.purchase_ledger or "Purchase"
            _sub(accounting_alloc, "LEDGERNAME", ledger_name)
            _sub(accounting_alloc, "ISDEEMEDPOSITIVE", "Yes")
            _sub(accounting_alloc, "AMOUNT", f"-{_amount(item.base_net_amount)}")

        # Party (Credit)
        party_entry = etree.SubElement(voucher, "LEDGERENTRIES.LIST")
        _sub(party_entry, "LEDGERNAME", inv.supplier_name)
        _sub(party_entry, "ISDEEMEDPOSITIVE", "No")
        _sub(party_entry, "AMOUNT", _amount(inv.base_grand_total))

        # Tax entries (Debit for input tax)
        for tax in doc.taxes:
            if flt(tax.tax_amount) != 0:
                tax_entry = etree.SubElement(voucher, "LEDGERENTRIES.LIST")
                ledger_name = _strip_company(tax.account_head)
                if "TDS" in ledger_name.upper():
                    ledger_name = settings.tds_deductible_ledger or "TDS-Deductible"
                _sub(tax_entry, "LEDGERNAME", ledger_name)
                
                # In Purchase, tax is typically a Debit (addition to cost/input credit) -> ISDEEMEDPOSITIVE=Yes
                tax_val = flt(tax.base_tax_amount_after_discount_amount)
                if not tax_val:
                    tax_val = flt(tax.base_tax_amount)
                
                is_deduction = tax.get("add_deduct_tax") == "Deduct" or tax_val < 0 or "TDS" in ledger_name.upper()
                is_credit = is_deduction # deduction from purchase total is a Credit
                
                _sub(tax_entry, "ISDEEMEDPOSITIVE", "No" if is_credit else "Yes")
                _sub(tax_entry, "AMOUNT", _amount(abs(tax_val)) if is_credit else f"-{_amount(abs(tax_val))}")

        count += 1

    return _to_xml_string(root), count


# ─────────────────────────────────────────────────────────────────────────────
# Payment Entry → Tally Receipt / Payment Voucher
# ─────────────────────────────────────────────────────────────────────────────

def generate_payment_entry_xml(from_date=None, to_date=None, company=None):
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _voucher_envelope(company, "Vouchers")

    filters = {"docstatus": 1, "company": company}
    if from_date and to_date:
        filters["posting_date"] = ["between", [from_date, to_date]]
    elif from_date:
        filters["posting_date"] = [">=", from_date]
    elif to_date:
        filters["posting_date"] = ["<=", to_date]

    payments = frappe.get_all(
        "Payment Entry",
        filters=filters,
        fields=["name", "payment_type", "party_type", "party", "party_name",
                "posting_date", "paid_amount", "received_amount",
                "paid_from", "paid_to", "paid_from_account_currency",
                "remarks", "reference_no"]
    )

    count = 0
    for pay in payments:
        # Determine voucher type
        if pay.payment_type == "Receive":
            vch_type = settings.receipt_voucher_type or "Receipt"
        elif pay.payment_type == "Pay":
            vch_type = settings.payment_voucher_type or "Payment"
        else:
            vch_type = settings.journal_voucher_type or "Journal"

        tally_dt = _tally_date(pay.posting_date)
        voucher = etree.SubElement(
            tallymessage, "VOUCHER",
            attrib={"REMOTEID": pay.name, "VCHTYPE": vch_type, "ACTION": "Create"}
        )
        _sub(voucher, "DATE", tally_dt)
        _sub(voucher, "EFFECTIVEDATE", tally_dt)
        _sub(voucher, "VOUCHERTYPENAME", vch_type)
        _sub(voucher, "VOUCHERNUMBER", pay.name)
        _sub(voucher, "PARTYLEDGERNAME", pay.party_name)
        _sub(voucher, "NARRATION", cstr(pay.remarks or f"Payment {pay.name}"))

        if pay.payment_type == "Receive":
            # Debit: Bank/Cash account
            bank_entry = etree.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _sub(bank_entry, "LEDGERNAME", _strip_company(pay.paid_to))
            _sub(bank_entry, "ISDEEMEDPOSITIVE", "Yes")
            _sub(bank_entry, "AMOUNT", f"-{_amount(pay.received_amount)}")
            # Credit: Party
            party_entry = etree.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _sub(party_entry, "LEDGERNAME", pay.party_name)
            _sub(party_entry, "ISDEEMEDPOSITIVE", "No")
            _sub(party_entry, "AMOUNT", _amount(pay.paid_amount))

        elif pay.payment_type == "Pay":
            # Debit: Party
            party_entry = etree.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _sub(party_entry, "LEDGERNAME", pay.party_name)
            _sub(party_entry, "ISDEEMEDPOSITIVE", "Yes")
            _sub(party_entry, "AMOUNT", f"-{_amount(pay.paid_amount)}")
            # Credit: Bank/Cash
            bank_entry = etree.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _sub(bank_entry, "LEDGERNAME", _strip_company(pay.paid_from))
            _sub(bank_entry, "ISDEEMEDPOSITIVE", "No")
            _sub(bank_entry, "AMOUNT", _amount(pay.received_amount))

        count += 1

    return _to_xml_string(root), count


def _strip_company(account_name):
    """ERPNext appends ' - CompanyAbbr'. Strip it for Tally."""
    if account_name and " - " in account_name:
        return account_name.rsplit(" - ", 1)[0]
    return account_name or ""


# ─────────────────────────────────────────────────────────────────────────────
# Journal Entry → Tally Journal Voucher
# ─────────────────────────────────────────────────────────────────────────────

def generate_journal_entry_xml(from_date=None, to_date=None, company=None):
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _voucher_envelope(company, "Vouchers")

    filters = {"docstatus": 1, "company": company}
    if from_date and to_date:
        filters["posting_date"] = ["between", [from_date, to_date]]
    elif from_date:
        filters["posting_date"] = [">=", from_date]
    elif to_date:
        filters["posting_date"] = ["<=", to_date]

    journals = frappe.get_all(
        "Journal Entry",
        filters=filters,
        fields=["name", "posting_date", "total_debit", "user_remark",
                "cheque_no", "cheque_date"]
    )

    count = 0
    for jv in journals:
        doc = frappe.get_doc("Journal Entry", jv.name)
        tally_dt = _tally_date(jv.posting_date)
        voucher = etree.SubElement(
            tallymessage, "VOUCHER",
            attrib={"REMOTEID": jv.name,
                    "VCHTYPE": settings.journal_voucher_type or "Journal",
                    "ACTION": "Create"}
        )
        _sub(voucher, "DATE", tally_dt)
        _sub(voucher, "EFFECTIVEDATE", tally_dt)
        _sub(voucher, "VOUCHERTYPENAME", settings.journal_voucher_type or "Journal")
        _sub(voucher, "VOUCHERNUMBER", jv.name)
        _sub(voucher, "NARRATION", cstr(jv.user_remark or f"Journal Entry {jv.name}"))

        for acc in doc.accounts:
            entry = etree.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            ledger_name = _strip_company(acc.account)
            _sub(entry, "LEDGERNAME", ledger_name)
            if flt(acc.debit_in_account_currency) > 0:
                _sub(entry, "ISDEEMEDPOSITIVE", "Yes")
                _sub(entry, "AMOUNT", f"-{_amount(acc.debit_in_account_currency)}")
            else:
                _sub(entry, "ISDEEMEDPOSITIVE", "No")
                _sub(entry, "AMOUNT", _amount(acc.credit_in_account_currency))

        count += 1

    return _to_xml_string(root), count


# ─────────────────────────────────────────────────────────────────────────────
# Bank Transaction → Tally Journal Voucher
# ─────────────────────────────────────────────────────────────────────────────

def generate_bank_transaction_xml(from_date=None, to_date=None, company=None):
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _voucher_envelope(company, "Vouchers")

    filters = {"docstatus": 1}
    if from_date and to_date:
        filters["date"] = ["between", [from_date, to_date]]
    elif from_date:
        filters["date"] = [">=", from_date]
    elif to_date:
        filters["date"] = ["<=", to_date]

    transactions = frappe.get_all(
        "Bank Transaction",
        filters=filters,
        fields=["name", "date", "bank_account", "deposit", "withdrawal",
                "description", "reference_number", "transaction_type"]
    )

    count = 0
    for txn in transactions:
        amount = flt(txn.deposit) if flt(txn.deposit) > 0 else flt(txn.withdrawal)
        is_deposit = flt(txn.deposit) > 0

        # Get the bank account ledger name
        bank_account_doc = frappe.get_cached_value(
            "Bank Account", txn.bank_account, "account"
        )
        bank_ledger = _strip_company(bank_account_doc) if bank_account_doc else txn.bank_account

        tally_dt = _tally_date(txn.date)
        voucher = etree.SubElement(
            tallymessage, "VOUCHER",
            attrib={"REMOTEID": txn.name,
                    "VCHTYPE": settings.journal_voucher_type or "Journal",
                    "ACTION": "Create"}
        )
        _sub(voucher, "DATE", tally_dt)
        _sub(voucher, "EFFECTIVEDATE", tally_dt)
        _sub(voucher, "VOUCHERTYPENAME", settings.journal_voucher_type or "Journal")
        _sub(voucher, "VOUCHERNUMBER", txn.reference_number or txn.name)
        _sub(voucher, "NARRATION", cstr(txn.description or f"Bank Transaction {txn.name}"))

        if is_deposit:
            bank_e = etree.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _sub(bank_e, "LEDGERNAME", bank_ledger)
            _sub(bank_e, "ISDEEMEDPOSITIVE", "Yes")
            _sub(bank_e, "AMOUNT", f"-{_amount(amount)}")

            suspense_e = etree.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _sub(suspense_e, "LEDGERNAME", "Suspense Account")
            _sub(suspense_e, "ISDEEMEDPOSITIVE", "No")
            _sub(suspense_e, "AMOUNT", _amount(amount))
        else:
            suspense_e = etree.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _sub(suspense_e, "LEDGERNAME", "Suspense Account")
            _sub(suspense_e, "ISDEEMEDPOSITIVE", "Yes")
            _sub(suspense_e, "AMOUNT", f"-{_amount(amount)}")

            bank_e = etree.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _sub(bank_e, "LEDGERNAME", bank_ledger)
            _sub(bank_e, "ISDEEMEDPOSITIVE", "No")
            _sub(bank_e, "AMOUNT", _amount(amount))

        count += 1

    return _to_xml_string(root), count


# ─────────────────────────────────────────────────────────────────────────────
# Units of Measure (UOM)
# ─────────────────────────────────────────────────────────────────────────────

def generate_uoms_xml(from_date=None, to_date=None, company=None):
    """Export Units of Measure to Tally."""
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _envelope(company)

    uoms = frappe.get_all("UOM", fields=["name", "uom_name"])
    count = 0
    for u in uoms:
        unit = etree.SubElement(
            tallymessage, "UNIT",
            attrib={"NAME": u.name, "ACTION": "Create"}
        )
        _sub(unit, "NAME", u.name)
        _sub(unit, "ISSIMPLEUNIT", "Yes")
        count += 1

    return _to_xml_string(root), count


# ─────────────────────────────────────────────────────────────────────────────
# Stock Items
# ─────────────────────────────────────────────────────────────────────────────

def generate_stock_groups_xml(from_date=None, to_date=None, company=None):
    """Export Item Groups to Tally as Stock Groups."""
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _envelope(company)
    applicable_from = _get_applicable_from_date(from_date)

    groups = frappe.get_all(
        "Item Group",
        fields=["name", "item_group_name", "parent_item_group"]
    )
    count = 0
    for grp in groups:
        stockgroup = etree.SubElement(
            tallymessage, "STOCKGROUP",
            attrib={"NAME": grp.item_group_name or grp.name, "ACTION": "Create"}
        )
        _sub(stockgroup, "NAME", grp.item_group_name or grp.name)
        
        parent = grp.parent_item_group
        if parent and parent != "All Item Groups":
            _sub(stockgroup, "PARENT", parent)
        else:
            _sub(stockgroup, "PARENT", "")
            
        _sub(stockgroup, "ISADDABLE", "Yes")
        
        # Default empty GST and HSN blocks for group, Tally will use 'As per Company' or let items override
        gst_list = etree.SubElement(stockgroup, "GSTDETAILS.LIST")
        _sub(gst_list, "APPLICABLEFROM", applicable_from)
        _sub(gst_list, "SRCOFGSTDETAILS", "As per Company/Stock Group")
        
        hsn_list = etree.SubElement(stockgroup, "HSNDETAILS.LIST")
        _sub(hsn_list, "APPLICABLEFROM", applicable_from)
        _sub(hsn_list, "SRCOFHSNDETAILS", "As per Company/Stock Group")
        
        count += 1

    return _to_xml_string(root), count


def _get_item_gst_rates(item_name, hsn_code):
    """Attempt to fetch IGST, CGST, SGST, Cess rates for an Item."""
    igst = cgst = sgst = cess = 0.0
    
    def _parse_taxes(taxes_list):
        _i = _c = _s = _ce = 0.0
        for tax in taxes_list:
            head = cstr(tax.get("tax_type") or tax.get("account_head") or tax.get("item_tax_template") or "").lower()
            rate = flt(tax.get("tax_rate") or tax.get("rate") or 0.0)
            if not rate:
                continue
            if "igst" in head or "integrated" in head: _i = max(_i, rate)
            elif "cgst" in head or "central" in head: _c = max(_c, rate)
            elif "sgst" in head or "utgst" in head or "state" in head: _s = max(_s, rate)
            elif "cess" in head: _ce = max(_ce, rate)
        return _i, _c, _s, _ce

    # 1. Try to fetch from GST HSN Code (India Compliance standard)
    if hsn_code:
        try:
            hsn = frappe.get_doc("GST HSN Code", hsn_code)
            if hasattr(hsn, "taxes") and hsn.taxes:
                igst, cgst, sgst, cess = _parse_taxes(hsn.taxes)
            
            # If HSN links to an item tax template directly
            if not igst and not cgst and hsn.get("item_tax_template"):
                template = frappe.get_doc("Item Tax Template", hsn.get("item_tax_template"))
                igst, cgst, sgst, cess = _parse_taxes(template.get("taxes", []))
                
            # Fallback for older fields
            if not igst and not cgst:
                igst = flt(hsn.get("integrated_tax") or hsn.get("igst_rate") or hsn.get("igst"))
                cgst = flt(hsn.get("central_tax") or hsn.get("cgst_rate") or hsn.get("cgst"))
                sgst = flt(hsn.get("state_tax") or hsn.get("sgst_rate") or hsn.get("sgst"))
                cess = flt(hsn.get("cess_amount") or hsn.get("cess_rate") or hsn.get("cess"))
        except Exception:
            pass

    # 2. Try Item Tax Template directly linked to Item
    if not igst and not cgst:
        try:
            item = frappe.get_doc("Item", item_name)
            
            # Common custom fields for gst rate
            rate = flt(item.get("gst_rate") or item.get("custom_gst_rate") or item.get("tax_rate"))
            if rate:
                igst = rate
                
            if not igst and not cgst:
                for t in item.get("taxes", []):
                    if t.item_tax_template:
                        template = frappe.get_doc("Item Tax Template", t.item_tax_template)
                        _i, _c, _s, _ce = _parse_taxes(template.get("taxes", []))
                        igst = max(igst, _i); cgst = max(cgst, _c); sgst = max(sgst, _s); cess = max(cess, _ce)
            
            # 3. Fallback: try Item Group
            if not igst and not cgst and item.item_group:
                ig_doc = frappe.get_doc("Item Group", item.item_group)
                
                # Check custom fields on item group
                rate = flt(ig_doc.get("gst_rate") or ig_doc.get("custom_gst_rate") or ig_doc.get("tax_rate"))
                if rate:
                    igst = rate
                
                if not igst and not cgst:
                    for t in ig_doc.get("taxes", []):
                        if t.item_tax_template:
                            template = frappe.get_doc("Item Tax Template", t.item_tax_template)
                            _i, _c, _s, _ce = _parse_taxes(template.get("taxes", []))
                            igst = max(igst, _i); cgst = max(cgst, _c); sgst = max(sgst, _s); cess = max(cess, _ce)
        except Exception:
            pass

    # Normalize rates
    if igst and not cgst:
        cgst = sgst = igst / 2.0
    elif cgst and not igst:
        igst = cgst + sgst
        
    return igst, cgst, sgst, cess


def _add_item_gst_and_hsn_details(parent_element, item_code, applicable_from, hsn_code=None):
    if not hsn_code:
        try:
            item_doc = frappe.get_doc("Item", item_code)
            hsn_code = item_doc.get("gst_hsn_code")
        except Exception:
            pass

    gst_list = etree.SubElement(parent_element, "GSTDETAILS.LIST")
    _sub(gst_list, "APPLICABLEFROM", applicable_from)
    
    igst, cgst, sgst, cess = _get_item_gst_rates(item_code, hsn_code)
    
    if igst > 0 or cgst > 0:
        _sub(gst_list, "TAXABILITY", "Taxable")
        _sub(gst_list, "SRCOFGSTDETAILS", "Specify Details Here")
        _sub(gst_list, "GSTCALCSLABONMRP", "No")
        _sub(gst_list, "ISREVERSECHARGEAPPLICABLE", "No")
        _sub(gst_list, "ISNONGSTGOODS", "No")
        _sub(gst_list, "GSTINELIGIBLEITC", "No")
        _sub(gst_list, "INCLUDEEXPFORSLABCALC", "No")
        
        state_wise = etree.SubElement(gst_list, "STATEWISEDETAILS.LIST")
        _sub(state_wise, "STATENAME", " Any")
        
        rate_cgst = etree.SubElement(state_wise, "RATEDETAILS.LIST")
        _sub(rate_cgst, "GSTRATEDUTYHEAD", "CGST")
        _sub(rate_cgst, "GSTRATEVALUATIONTYPE", "Based on Value")
        _sub(rate_cgst, "GSTRATE", f" {cgst:g}")
        
        rate_sgst = etree.SubElement(state_wise, "RATEDETAILS.LIST")
        _sub(rate_sgst, "GSTRATEDUTYHEAD", "SGST/UTGST")
        _sub(rate_sgst, "GSTRATEVALUATIONTYPE", "Based on Value")
        _sub(rate_sgst, "GSTRATE", f" {sgst:g}")
        
        rate_igst = etree.SubElement(state_wise, "RATEDETAILS.LIST")
        _sub(rate_igst, "GSTRATEDUTYHEAD", "IGST")
        _sub(rate_igst, "GSTRATEVALUATIONTYPE", "Based on Value")
        _sub(rate_igst, "GSTRATE", f" {igst:g}")
        
        rate_cess = etree.SubElement(state_wise, "RATEDETAILS.LIST")
        _sub(rate_cess, "GSTRATEDUTYHEAD", "Cess")
        if cess > 0:
            _sub(rate_cess, "GSTRATEVALUATIONTYPE", "Based on Value")
            _sub(rate_cess, "GSTRATE", f" {cess:g}")
        else:
            _sub(rate_cess, "GSTRATEVALUATIONTYPE", " Not Applicable")
            
        rate_scess = etree.SubElement(state_wise, "RATEDETAILS.LIST")
        _sub(rate_scess, "GSTRATEDUTYHEAD", "State Cess")
        _sub(rate_scess, "GSTRATEVALUATIONTYPE", "Based on Value")
        
        etree.SubElement(state_wise, "GSTSLABRATES.LIST")
        etree.SubElement(gst_list, "TEMPGSTITEMSLABRATES.LIST")
        etree.SubElement(gst_list, "TEMPGSTDETAILSLABRATES.LIST")
    else:
        _sub(gst_list, "TAXABILITY", "Taxable")
        _sub(gst_list, "SRCOFGSTDETAILS", "As per Company/Stock Group")
        
    hsn_list = etree.SubElement(parent_element, "HSNDETAILS.LIST")
    _sub(hsn_list, "APPLICABLEFROM", applicable_from)
    if hsn_code:
        _sub(hsn_list, "HSNCODE", cstr(hsn_code))
        _sub(hsn_list, "SRCOFHSNDETAILS", "Specify Details Here")
    else:
        _sub(hsn_list, "SRCOFHSNDETAILS", "As per Company/Stock Group")


def generate_stock_items_xml(from_date=None, to_date=None, company=None):
    """Export Stock Items to Tally with enhanced details like GST and HSN."""
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _envelope(company)
    applicable_from = _get_applicable_from_date(from_date)

    # Fetching item fields including India compliance GST/HSN fields if they exist
    fields = ["name", "item_name", "item_group", "stock_uom", "description", "standard_rate", "is_stock_item", "has_batch_no", "valuation_method"]
    if frappe.db.has_column("Item", "gst_hsn_code"):
        fields.append("gst_hsn_code")

    items = frappe.get_all("Item", fields=fields)
    count = 0
    
    for item in items:
        stockitem = etree.SubElement(
            tallymessage, "STOCKITEM",
            attrib={"NAME": item.item_name or item.name, "ACTION": "Create"}
        )
        _sub(stockitem, "NAME", item.item_name or item.name)
        _sub(stockitem, "PARENT", item.item_group or "Primary")
        _sub(stockitem, "BASEUNITS", item.stock_uom or "Nos")
        _sub(stockitem, "DESCRIPTION", cstr(item.description))
        
        # Type of Supply
        type_of_supply = "Goods" if item.is_stock_item else "Services"
        _sub(stockitem, "GSTTYPEOFSUPPLY", type_of_supply)
        _sub(stockitem, "GSTAPPLICABLE", "Applicable")
        
        # Batch and Valuation
        is_batch = "Yes" if item.has_batch_no else "No"
        _sub(stockitem, "ISBATCHWISEON", is_batch)
        
        val_method = "Avg. Cost"
        if item.valuation_method == "FIFO":
            val_method = "FIFO"
        _sub(stockitem, "COSTINGMETHOD", val_method)
        _sub(stockitem, "VALUATIONMETHOD", "Avg. Price")
        _sub(stockitem, "ISDELETED", "No")

        # GST and HSN Details
        _add_item_gst_and_hsn_details(stockitem, item.name, applicable_from, item.get("gst_hsn_code"))
            
        count += 1

    return _to_xml_string(root), count


# ─────────────────────────────────────────────────────────────────────────────
# Master export function
# ─────────────────────────────────────────────────────────────────────────────

def generate_full_export_xml(from_date=None, to_date=None, company=None):
    """Combine all XML exports into a single ENVELOPE."""
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")

    root = etree.Element("ENVELOPE")
    header = etree.SubElement(root, "HEADER")
    _sub(header, "TALLYREQUEST", "Import Data")
    body = etree.SubElement(root, "BODY")
    importdata = etree.SubElement(body, "IMPORTDATA")
    requestdesc = etree.SubElement(importdata, "REQUESTDESC")
    _sub(requestdesc, "REPORTNAME", "All Masters")
    staticvariables = etree.SubElement(requestdesc, "STATICVARIABLES")
    _sub(staticvariables, "SVCURRENTCOMPANY", company)
    requestdata = etree.SubElement(importdata, "REQUESTDATA")
    tallymessage = etree.SubElement(requestdata, "TALLYMESSAGE",
                                    nsmap={"UDF": "TallyUDF"})

    total = 0
    generators = [
        (settings.get("include_uoms", 1), generate_uoms_xml, {"from_date": from_date, "to_date": to_date}),
        (settings.get("include_stock_groups", 1), generate_stock_groups_xml, {"from_date": from_date, "to_date": to_date}),
        (settings.get("include_stock_items", 1), generate_stock_items_xml, {"from_date": from_date, "to_date": to_date}),
        (settings.include_chart_of_accounts, generate_chart_of_accounts_xml, {"from_date": from_date, "to_date": to_date}),
        (settings.include_parties, generate_parties_xml, {"from_date": from_date, "to_date": to_date}),
        (settings.include_sales_invoice, generate_sales_invoice_xml,
         {"from_date": from_date, "to_date": to_date}),
        (settings.include_purchase_invoice, generate_purchase_invoice_xml,
         {"from_date": from_date, "to_date": to_date}),
        (settings.include_payment_entry, generate_payment_entry_xml,
         {"from_date": from_date, "to_date": to_date}),
        (settings.include_journal_entry, generate_journal_entry_xml,
         {"from_date": from_date, "to_date": to_date}),
        (settings.include_bank_transaction, generate_bank_transaction_xml,
         {"from_date": from_date, "to_date": to_date}),
    ]

    for enabled, fn, kwargs in generators:
        if not enabled:
            continue
        try:
            xml_str, count = fn(company=company, **kwargs)
            # Parse the child TALLYMESSAGE and merge its children
            child_root = etree.fromstring(xml_str.encode("utf-8"))
            child_tm = child_root.find(".//TALLYMESSAGE")
            if child_tm is not None:
                for child in child_tm:
                    tallymessage.append(child)
            total += count
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), f"Tally Bridge: {fn.__name__} failed")

    return _to_xml_string(root), total
