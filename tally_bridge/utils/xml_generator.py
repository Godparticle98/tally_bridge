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


def generate_chart_of_accounts_xml(company=None):
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
        "SEZ": "SEZ",
        "Overseas": "Overseas",
        "Consumer": "Consumer",
        "Deemed Export": "Deemed Exports - Refund",
        "UIN Holders": "UIN Holders",
        "Tax Deductor": "Tax Deductor",
    }
    return mapping.get(gst_category, "Regular") if gst_category else "Regular"


def _add_party_ledger(tallymessage, party_name, parent_group, addr, tax_id):
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
    
    # Hardcoded defaults from Tally XML
    defaults = [
        ("GSTTYPE", "&#4; Not Applicable"),
        ("APPROPRIATEFOR", "&#4; Not Applicable"),
        ("GSTNATUREOFSUPPLY", "&#4; Not Applicable"),
        ("SERVICECATEGORY", "&#4; Not Applicable"),
        ("EXCISELEDGERCLASSIFICATION", "&#4; Not Applicable"),
        ("EXCISEDUTYTYPE", "&#4; Not Applicable"),
        ("EXCISENATUREOFPURCHASE", "&#4; Not Applicable"),
        ("TDSDEDUCTEETYPE", "Company - Resident"),
        ("LEDGERFBTCATEGORY", "&#4; Not Applicable"),
        ("ISBILLWISEON", "Yes"),
        ("ISCOSTCENTRESON", "No"),
        ("ISINTERESTON", "No"),
        ("ALLOWINMOBILE", "No"),
        ("ISCOSTTRACKINGON", "No"),
        ("ISBENEFICIARYCODEON", "No"),
        ("ISEXPORTONVCHCREATE", "No"),
        ("PLASINCOMEEXPENSE", "No"),
        ("ISUPDATINGTARGETID", "No"),
        ("ISDELETED", "No"),
        ("ISSECURITYONWHENENTERED", "No"),
        ("ASORIGINAL", "Yes"),
        ("ISCONDENSED", "No"),
        ("AFFECTSSTOCK", "No"),
        ("ISRATEINCLUSIVEVAT", "No"),
        ("FORPAYROLL", "No"),
        ("ISABCENABLED", "No"),
        ("ISCREDITDAYSCHKON", "No"),
        ("INTERESTONBILLWISE", "No"),
        ("OVERRIDEINTEREST", "No"),
        ("OVERRIDEADVINTEREST", "No"),
        ("USEFORVAT", "No"),
        ("IGNORETDSEXEMPT", "No"),
        ("ISTCSAPPLICABLE", "No"),
        ("ISTDSAPPLICABLE", "Yes"),
        ("ISFBTAPPLICABLE", "No"),
        ("ISGSTAPPLICABLE", "No"),
        ("ISEXCISEAPPLICABLE", "No"),
        ("ISTDSEXPENSE", "No"),
        ("ISEDLIAPPLICABLE", "No"),
        ("ISRELATEDPARTY", "No"),
        ("USEFORESIELIGIBILITY", "No"),
        ("ISINTERESTINCLLASTDAY", "No"),
        ("APPROPRIATETAXVALUE", "No"),
        ("ISBEHAVEASDUTY", "No"),
        ("INTERESTINCLDAYOFADDITION", "No"),
        ("INTERESTINCLDAYOFDEDUCTION", "No"),
        ("ISOTHTERRITORYASSESSEE", "No"),
        ("IGNOREMISMATCHWITHWARNING", "No"),
        ("USEASNOTIONALBANK", "No"),
        ("BEHAVEASPAYMENTGATEWAY", "No"),
        ("OVERRIDECREDITLIMIT", "No"),
        ("ISAGAINSTFORMC", "No"),
        ("ISCHEQUEPRINTINGENABLED", "Yes"),
        ("ISPAYUPLOAD", "No"),
        ("ISPAYBATCHONLYSAL", "No"),
        ("ISBNFCODESUPPORTED", "No"),
        ("ALLOWEXPORTWITHERRORS", "No"),
        ("CONSIDERPURCHASEFOREXPORT", "No"),
        ("ISTRANSPORTER", "No"),
        ("ISECASHLEDGER", "No"),
        ("USEFORNOTIONALITC", "No"),
        ("ISECOMMOPERATOR", "No"),
        ("OVERRIDEBASEDONREALIZATION", "No"),
        ("ISECDIFFINSDATE", "No"),
        ("SHOWINPAYSLIP", "No"),
        ("USEFORGRATUITY", "No"),
        ("ISTDSPROJECTED", "No"),
        ("ISSALARYMULFILE", "No"),
        ("FORSERVICETAX", "No"),
        ("ISINPUTCREDIT", "No"),
        ("ISEXEMPTED", "No"),
        ("ISABATEMENTAPPLICABLE", "No"),
        ("ISSTXPARTY", "No"),
        ("ISSTXNONREALIZEDTYPE", "No"),
        ("USEFORKKC", "No"),
        ("USEFORSBC", "No"),
        ("ISUSEDFORCVD", "No"),
        ("LEDBELONGSTONONTAXABLE", "No"),
        ("ISEXCISEMERCHANTEXPORTER", "No"),
        ("ISPARTYEXEMPTED", "No"),
        ("ISSEZPARTY", "No"),
        ("TDSDEDUCTEEISSPECIALRATE", "No"),
        ("ISECHEQUESUPPORTED", "No"),
        ("ISEDDSUPPORTED", "No"),
        ("HASECHEQUEDELIVERYMODE", "No"),
        ("HASECHEQUEDELIVERYTO", "No"),
        ("HASECHEQUEPRINTLOCATION", "No"),
        ("HASECHEQUEPAYABLELOCATION", "No"),
        ("HASECHEQUEBANKLOCATION", "No"),
        ("HASEDDDELIVERYMODE", "No"),
        ("HASEDDDELIVERYTO", "No"),
        ("HASEDDPRINTLOCATION", "No"),
        ("HASEDDPAYABLELOCATION", "No"),
        ("HASEDDBANKLOCATION", "No"),
        ("ISEBANKINGENABLED", "No"),
        ("ISEXPORTFILEENCRYPTED", "No"),
        ("ISBATCHENABLED", "No"),
        ("ISPRODUCTCODEBASED", "No"),
        ("HASEDDCITY", "No"),
        ("HASECHEQUECITY", "No"),
        ("ISFILENAMEFORMATSUPPORTED", "No"),
        ("HASCLIENTCODE", "No"),
        ("PAYINSISBATCHAPPLICABLE", "No"),
        ("PAYINSISFILENUMAPP", "No"),
        ("ISSALARYTRANSGROUPEDFORBRS", "No"),
        ("ISEBANKINGSUPPORTED", "No"),
        ("ISSCBUAE", "No"),
        ("ISBANKSTATUSAPP", "No"),
        ("ISSALARYGROUPED", "No"),
        ("USEFORPURCHASETAX", "No"),
        ("BANKISRECONCILEPERFECTMATCHES", "No"),
        ("ISPYMTADVONLINE", "No"),
        ("ISPYMTADVCCENABLED", "No"),
        ("ISINCLUDEPYMTADVBILLWISE", "No"),
        ("AUDITED", "No")
    ]
    for tag, val in defaults:
        _sub(ledger, tag, val)
        
    # Empty Lists
    empty_lists = [
        "SERVICETAXDETAILS.LIST", "LBTREGNDETAILS.LIST", "VATDETAILS.LIST",
        "SALESTAXCESSDETAILS.LIST", "GSTDETAILS.LIST", "HSNDETAILS.LIST",
        "MSMEREGISTRATIONDETAILS.LIST", "XBRLDETAIL.LIST", "AUDITDETAILS.LIST",
        "SCHVIDETAILS.LIST", "EXCISETARIFFDETAILS.LIST", "TCSCATEGORYDETAILS.LIST",
        "TDSCATEGORYDETAILS.LIST", "SLABPERIOD.LIST", "GRATUITYPERIOD.LIST",
        "ADDITIONALCOMPUTATIONS.LIST", "EXCISEJURISDICTIONDETAILS.LIST",
        "EXCLUDEDTAXATIONS.LIST", "BANKALLOCATIONS.LIST", "PAYMENTDETAILS.LIST",
        "BANKEXPORTFORMATS.LIST", "TRANSFERMODELIMITDETAILS.LIST", "BILLALLOCATIONS.LIST",
        "INTERESTCOLLECTION.LIST", "LEDGERCLOSINGVALUES.LIST", "LEDGERAUDITCLASS.LIST",
        "OLDAUDITENTRIES.LIST", "TDSEXEMPTIONRULES.LIST", "LOWERDEDUCTION.LIST",
        "STXABATEMENTDETAILS.LIST", "LEDMULTIADDRESSLIST.LIST", "STXTAXDETAILS.LIST",
        "CHEQUERANGE.LIST", "DEFAULTVCHCHEQUEDETAILS.LIST", "ACCOUNTAUDITENTRIES.LIST",
        "AUDITENTRIES.LIST", "BRSIMPORTEDINFO.LIST", "AUTOBRSCONFIGS.LIST",
        "BANKURENTRIES.LIST", "DEFAULTCHEQUEDETAILS.LIST", "DEFAULTOPENINGCHEQUEDETAILS.LIST",
        "CANCELLEDPAYALLOCATIONS.LIST", "ECHEQUEPRINTLOCATION.LIST", "ECHEQUEPAYABLELOCATION.LIST",
        "EDDPRINTLOCATION.LIST", "EDDPAYABLELOCATION.LIST", "AVAILABLETRANSACTIONTYPES.LIST",
        "LEDPAYINSCONFIGS.LIST", "TYPECODEDETAILS.LIST", "FIELDVALIDATIONDETAILS.LIST",
        "INPUTCRALLOCS.LIST", "TCSMETHODOFCALCULATION.LIST", "GSTRECONPREFIXSUFFIXDETAILS.LIST",
        "GSTCLASSFNIGSTRATES.LIST", "EXTARIFFDUTYHEADDETAILS.LIST", "TEMPGSTITEMSLABRATES.LIST",
        "LEDGSTADDRESS.LIST", "VOUCHERTYPEPRODUCTCODES.LIST", "LEDADDRESS.LIST",
        "DEFMULTIPLETOPHONENO.LIST"
    ]
    for tag in empty_lists:
        etree.SubElement(ledger, tag)

    # GST registration block
    gstin = addr["gstin"] or tax_id or ""
    if gstin:
        gst_type = _gst_reg_type(addr["gst_category"])
        gst_list = etree.SubElement(ledger, "LEDGSTREGDETAILS.LIST")
        _sub(gst_list, "APPLICABLEFROM", _tally_date(frappe.utils.today()))
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
    _sub(mail_list, "APPLICABLEFROM", _tally_date(frappe.utils.today()))
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
    _sub(deduct_rules, "NATUREOFPAYMENT", "&#4; All Items")
    
    contact_details = etree.SubElement(ledger, "CONTACTDETAILS.LIST")
    _sub(contact_details, "NAME", "Primary Mobile No.")
    _sub(contact_details, "COUNTRYISDCODE", "+91")
    _sub(contact_details, "ISDEFAULTWHATSAPPNUM", "Yes")


def generate_parties_xml(company=None):
    """Export Customers and Suppliers as Tally ledgers under Sundry Debtors/Creditors.
    Includes mailing address, state, country, pincode, GSTIN and GST registration type.
    """
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _envelope(company)
    count = 0

    customers = frappe.get_all(
        "Customer",
        fields=["name", "customer_name", "customer_group", "tax_id"]
    )
    for cust in customers:
        addr = _get_party_address("Customer", cust.name)
        _add_party_ledger(tallymessage, cust.customer_name, settings.sundry_debtors_ledger or "Sundry Debtors", addr, cust.tax_id)
        count += 1

    suppliers = frappe.get_all(
        "Supplier",
        fields=["name", "supplier_name", "supplier_group", "tax_id"]
    )
    for sup in suppliers:
        addr = _get_party_address("Supplier", sup.name)
        _add_party_ledger(tallymessage, sup.supplier_name, settings.sundry_creditors_ledger or "Sundry Creditors", addr, sup.tax_id)
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
    if from_date:
        filters["posting_date"] = [">=", from_date]
    if to_date:
        filters.setdefault("posting_date", [">=", from_date])
        filters["posting_date"] = ["between", [from_date, to_date]]

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
        _sub(voucher, "NARRATION", cstr(inv.remarks or f"Sales Invoice {inv.name}"))
        _sub(voucher, "ISINVOICE", "Yes")

        # Inventory entries (Items)
        for item in doc.items:
            inventory_entry = etree.SubElement(voucher, "ALLINVENTORYENTRIES.LIST")
            _sub(inventory_entry, "STOCKITEMNAME", item.item_name or item.item_code)
            _sub(inventory_entry, "ISDEEMEDPOSITIVE", "No")  # Outward / sales = No

            _sub(inventory_entry, "RATE", f"{_amount(item.rate)}/{item.uom}")
            # Sales: item amount is a CREDIT in the income account → positive value
            _sub(inventory_entry, "AMOUNT", _amount(item.base_amount))
            _sub(inventory_entry, "ACTUALQTY", f" {item.qty} {item.uom}")
            _sub(inventory_entry, "BILLEDQTY", f" {item.qty} {item.uom}")

            # Accounting Allocation — REQUIRED by Tally for ISINVOICE=Yes vouchers.
            # Each inventory entry must have at least one ACCOUNTINGALLOCATIONS.LIST child.
            # The income ledger is CREDITED (ISDEEMEDPOSITIVE=No) with a positive amount.
            accounting_alloc = etree.SubElement(inventory_entry, "ACCOUNTINGALLOCATIONS.LIST")
            ledger_name = _strip_company(item.income_account) if item.income_account else (settings.sales_ledger or "Sales")
            _sub(accounting_alloc, "LEDGERNAME", ledger_name)
            _sub(accounting_alloc, "ISDEEMEDPOSITIVE", "No")   # Credit → No
            _sub(accounting_alloc, "AMOUNT", _amount(item.base_amount))  # Positive credit amount

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
                _sub(tax_entry, "LEDGERNAME", _strip_company(tax.account_head))
                
                # In ERPNext: base_tax_amount_after_discount_amount holds the actual tax value added/deducted.
                # If negative, it's a deduction (Debit -> ISDEEMEDPOSITIVE=Yes, Amount=-ve)
                # If positive, it's an addition (Credit -> ISDEEMEDPOSITIVE=No, Amount=+ve)
                tax_val = flt(tax.base_tax_amount_after_discount_amount)
                if not tax_val:
                    tax_val = flt(tax.base_tax_amount)
                
                is_debit = tax_val < 0
                _sub(tax_entry, "ISDEEMEDPOSITIVE", "Yes" if is_debit else "No")
                _sub(tax_entry, "AMOUNT", f"-{_amount(abs(tax_val))}" if is_debit else _amount(tax_val))

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
        _sub(voucher, "NARRATION", cstr(inv.remarks or f"Purchase Invoice {inv.name}"))
        _sub(voucher, "ISINVOICE", "Yes")

        # Inventory entries (Items)
        for item in doc.items:
            inventory_entry = etree.SubElement(voucher, "ALLINVENTORYENTRIES.LIST")
            _sub(inventory_entry, "STOCKITEMNAME", item.item_name or item.item_code)
            _sub(inventory_entry, "ISDEEMEDPOSITIVE", "Yes") # Inward is Yes
            
            _sub(inventory_entry, "RATE", f"{_amount(item.rate)}/{item.uom}")
            _sub(inventory_entry, "AMOUNT", f"-{_amount(item.base_amount)}") # Debit -> negative
            _sub(inventory_entry, "ACTUALQTY", f" {item.qty} {item.uom}")
            _sub(inventory_entry, "BILLEDQTY", f" {item.qty} {item.uom}")
            
            # Accounting Allocation for the Purchase Ledger
            accounting_alloc = etree.SubElement(inventory_entry, "ACCOUNTINGALLOCATIONS.LIST")
            ledger_name = _strip_company(item.expense_account) if item.expense_account else "Purchase"
            _sub(accounting_alloc, "LEDGERNAME", ledger_name)
            _sub(accounting_alloc, "ISDEEMEDPOSITIVE", "Yes")
            _sub(accounting_alloc, "AMOUNT", f"-{_amount(item.base_amount)}")

        # Party (Credit)
        party_entry = etree.SubElement(voucher, "LEDGERENTRIES.LIST")
        _sub(party_entry, "LEDGERNAME", inv.supplier_name)
        _sub(party_entry, "ISDEEMEDPOSITIVE", "No")
        _sub(party_entry, "AMOUNT", _amount(inv.base_grand_total))

        # Tax entries (Debit for input tax)
        for tax in doc.taxes:
            if flt(tax.tax_amount) != 0:
                tax_entry = etree.SubElement(voucher, "LEDGERENTRIES.LIST")
                _sub(tax_entry, "LEDGERNAME", _strip_company(tax.account_head))
                
                # In Purchase, tax is typically a Debit (addition to cost/input credit) -> ISDEEMEDPOSITIVE=Yes
                tax_val = flt(tax.base_tax_amount_after_discount_amount)
                if not tax_val:
                    tax_val = flt(tax.base_tax_amount)
                
                is_credit = tax_val < 0 # deduction from purchase total is a Credit
                _sub(tax_entry, "ISDEEMEDPOSITIVE", "No" if is_credit else "Yes")
                _sub(tax_entry, "AMOUNT", _amount(abs(tax_val)) if is_credit else f"-{_amount(tax_val)}")

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

def generate_uoms_xml(company=None):
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

def generate_stock_items_xml(company=None):
    """Export Stock Items to Tally."""
    settings = _settings()
    company = company or frappe.defaults.get_user_default("Company")
    root, tallymessage = _envelope(company)

    items = frappe.get_all(
        "Item",
        fields=["name", "item_name", "item_group", "stock_uom", "description", "standard_rate"]
    )
    count = 0
    for item in items:
        # Generate item group if needed, but Tally allows basic import without predefined group if we use Primary
        # For safety, map to Primary if the item_group doesn't exist in Tally, or let Tally handle it
        stockitem = etree.SubElement(
            tallymessage, "STOCKITEM",
            attrib={"NAME": item.item_name or item.name, "ACTION": "Create"}
        )
        _sub(stockitem, "NAME", item.item_name or item.name)
        _sub(stockitem, "PARENT", item.item_group or "Primary")
        _sub(stockitem, "BASEUNITS", item.stock_uom or "Nos")
        _sub(stockitem, "DESCRIPTION", cstr(item.description))
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
        (settings.get("include_uoms", 1), generate_uoms_xml, {}),
        (settings.get("include_stock_items", 1), generate_stock_items_xml, {}),
        (settings.include_chart_of_accounts, generate_chart_of_accounts_xml, {}),
        (settings.include_parties, generate_parties_xml, {}),
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
