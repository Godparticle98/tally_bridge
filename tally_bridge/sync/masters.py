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
    """Render party mailing/statutory details using Tally's ledger XML structure.

    Tally's documented ledger import examples and its current ledger-master
    XML template use ADDRESS.LIST plus COUNTRYNAME/STATENAME/PINCODE at the
    ledger level, and repeat the mailing details under
    LEDMAILINGDETAILS.LIST. We intentionally emit both structures so CREATE
    and ALTER follow the same master schema.
    """
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

    lines = _address_lines(address)

    # Core ledger mailing fields. These are the fields used by Tally's
    # documented ledger-master XML structure.
    if lines:
        addr_list = SubElement(ledger, "ADDRESS.LIST", {"TYPE": "String"})
        for line in lines:
            _text(addr_list, "ADDRESS", line)

    if country:
        _text(ledger, "COUNTRYNAME", country)
    if state:
        _text(ledger, "STATENAME", state)
    if pincode:
        _text(ledger, "PINCODE", pincode)

    if pan:
        _text(ledger, "INCOMETAXNUMBER", pan)

    _text(ledger, "ISBILLWISEON", "Yes")

    gst_registration_type = _gst_registration_type(doc)
    if gst_registration_type or gstin or state:
        gst = SubElement(ledger, "LEDGSTREGDETAILS.LIST")
        _text(gst, "APPLICABLEFROM", _effective_date(doc))
        if gst_registration_type:
            _text(gst, "GSTREGISTRATIONTYPE", gst_registration_type)
        if state:
            _text(gst, "STATE", state)
        if gstin:
            _text(gst, "GSTIN", gstin)

    # TallyPrime 3.x+ mailing-details structure. Keep this identical for
    # CREATE and ALTER so an ALTER cannot accidentally drop address data.
    mailing = SubElement(ledger, "LEDMAILINGDETAILS.LIST")
    if lines:
        mailing_addresses = SubElement(
            mailing, "ADDRESS.LIST", {"TYPE": "String"}
        )
        for line in lines:
            _text(mailing_addresses, "ADDRESS", line)

    _text(mailing, "APPLICABLEFROM", _effective_date(doc))
    _text(mailing, "MAILINGNAME", display_name or "")
    if state:
        _text(mailing, "STATE", state)
    if country:
        _text(mailing, "COUNTRY", country)
    if pincode:
        _text(mailing, "PINCODE", pincode)



def _ledger_name_list(ledger, display_name: str):
    names = SubElement(ledger, "NAME.LIST", {"TYPE": "String"})
    _text(names, "NAME", display_name)


def _party_ledger(
    doc,
    company: str,
    parent: str,
    display_name: str,
    action="Create",
    tally_name: str | None = None,
) -> str:
    # Tally's NAME attribute identifies the existing master for ALTER.
    # NAME.LIST carries the human-readable master name, allowing an ALTER to rename it.
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    target_name = tally_name or display_name
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    ledger = SubElement(m, "LEDGER", {"NAME": target_name, "ACTION": action.upper()})
    _text(ledger, "NAME", display_name)
    _text(ledger, "PARENT", parent)
    _add_party_details(ledger, doc, display_name)
    return _envelope([m], company, remote)


def customer(doc, company: str, action="Create", tally_name: str | None = None) -> str:
    display_name = getattr(doc, "customer_name", None) or doc.name
    return _party_ledger(doc, company, "Sundry Debtors", display_name, action, tally_name)


def supplier(doc, company: str, action="Create", tally_name: str | None = None) -> str:
    display_name = getattr(doc, "supplier_name", None) or doc.name
    return _party_ledger(doc, company, "Sundry Creditors", display_name, action, tally_name)


# TallyPrime UQC mappings for common ERPNext UOM names.
# The UQC value must be the exact Tally/GST code shown in the UQC selector.
_UQC_MAP = {
    "nos": "NOS-NUMBERS",
    "no": "NOS-NUMBERS",
    "number": "NOS-NUMBERS",
    "numbers": "NOS-NUMBERS",
    "pc": "PCS-PIECES",
    "pcs": "PCS-PIECES",
    "piece": "PCS-PIECES",
    "pieces": "PCS-PIECES",
    "kg": "KGS-KILOGRAMS",
    "kgs": "KGS-KILOGRAMS",
    "kilogram": "KGS-KILOGRAMS",
    "kilograms": "KGS-KILOGRAMS",
    "g": "GMS-GRAMMES",
    "gm": "GMS-GRAMMES",
    "gms": "GMS-GRAMMES",
    "gram": "GMS-GRAMMES",
    "grams": "GMS-GRAMMES",
    "mg": "MGS-MILLIGRAMMES",
    "milligram": "MGS-MILLIGRAMMES",
    "milligrams": "MGS-MILLIGRAMMES",
    "m": "MTR-METERS",
    "meter": "MTR-METERS",
    "meters": "MTR-METERS",
    "metre": "MTR-METERS",
    "metres": "MTR-METERS",
    "cm": "CMS-CENTIMETERS",
    "centimeter": "CMS-CENTIMETERS",
    "centimeters": "CMS-CENTIMETERS",
    "centimetre": "CMS-CENTIMETERS",
    "centimetres": "CMS-CENTIMETERS",
    "mm": "MMT-MILLIMETERS",
    "millimeter": "MMT-MILLIMETERS",
    "millimeters": "MMT-MILLIMETERS",
    "millimetre": "MMT-MILLIMETERS",
    "millimetres": "MMT-MILLIMETERS",
    "km": "KME-KILOMETRE",
    "kilometer": "KME-KILOMETRE",
    "kilometers": "KME-KILOMETRE",
    "kilometre": "KME-KILOMETRE",
    "kilometres": "KME-KILOMETRE",
    "l": "LTR-LITRES",
    "ltr": "LTR-LITRES",
    "liter": "LTR-LITRES",
    "liters": "LTR-LITRES",
    "litre": "LTR-LITRES",
    "litres": "LTR-LITRES",
    "ml": "MLT-MILILITRE",
    "milliliter": "MLT-MILILITRE",
    "milliliters": "MLT-MILILITRE",
    "millilitre": "MLT-MILILITRE",
    "millilitres": "MLT-MILILITRE",
    "sq ft": "SQF-SQUARE FEET",
    "square foot": "SQF-SQUARE FEET",
    "square feet": "SQF-SQUARE FEET",
    "sq m": "SQM-SQUARE METERS",
    "square meter": "SQM-SQUARE METERS",
    "square meters": "SQM-SQUARE METERS",
    "square metre": "SQM-SQUARE METERS",
    "square metres": "SQM-SQUARE METERS",
    "sq yd": "SQY-SQUARE YARDS",
    "square yard": "SQY-SQUARE YARDS",
    "square yards": "SQY-SQUARE YARDS",
    "yard": "YDS-YARDS",
    "yards": "YDS-YARDS",
    "foot": "FOT-FOOT",
    "feet": "FOT-FOOT",
    "inch": "INH-INCHES",
    "inches": "INH-INCHES",
    "cubic meter": "CBM-CUBIC METERS",
    "cubic meters": "CBM-CUBIC METERS",
    "cubic metre": "CBM-CUBIC METERS",
    "cubic metres": "CBM-CUBIC METERS",
    "cubic centimeter": "CCM-CUBIC CENTIMETERS",
    "cubic centimeters": "CCM-CUBIC CENTIMETERS",
    "cubic inch": "CIN-CUBIC INCHES",
    "cubic inches": "CIN-CUBIC INCHES",
    "ton": "TON-TONNES",
    "tons": "TON-TONNES",
    "tonne": "TON-TONNES",
    "tonnes": "TON-TONNES",
    "quintal": "QTL-QUINTALS",
    "quintals": "QTL-QUINTALS",
    "dozen": "DOZ-DOZENS",
    "pair": "PRS-PAIRS",
    "pairs": "PRS-PAIRS",
    "box": "BOX-BOXES",
    "boxes": "BOX-BOXES",
    "set": "SET-SETS",
    "sets": "SET-SETS",
}

import hashlib
import re


def _uom_key(value) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _clean_formal_name(value) -> str:
    """Return a Tally-safe formal name: letters/numbers/spaces only."""
    value = re.sub(r"[^A-Za-z0-9 ]+", " ", str(value or ""))
    value = " ".join(value.split())
    return value or "Unit"


def _alpha_hash(value, length=3) -> str:
    digest = hashlib.sha1(str(value).encode("utf-8")).digest()
    return "".join(chr(ord("A") + (byte % 26)) for byte in digest[:length])


def _uom_symbol(value) -> str:
    """Generate a deterministic 3-8 letter Tally symbol for an ERPNext UOM."""
    key = _uom_key(value)
    if key in _UQC_MAP:
        return _UQC_MAP[key].split("-", 1)[0]

    words = re.findall(r"[A-Za-z]+", str(value or "").upper())
    initials = "".join(word[0] for word in words)
    if len(initials) < 3:
        letters = "".join(words)
        initials = (letters + "UOM")[:3]
    initials = initials[:5]
    # Always append alphabetic entropy for non-standard units. This prevents
    # collisions such as Ounce/Gallon (US) vs Ounce/Gallon (UK) while keeping
    # the symbol within GST/Tally's 3-8 alphabetic-character constraint.
    symbol = (initials + _alpha_hash(value, 3))[:8]
    return symbol if len(symbol) >= 3 else "UOM"


def _uom_uqc(value) -> str:
    key = _uom_key(value)
    return _UQC_MAP.get(key, "OTH-OTHERS")


def _uom_definition(doc):
    original = getattr(doc, "uom_name", None) or getattr(doc, "name", None) or str(doc)
    symbol = _uom_symbol(original)
    formal = _clean_formal_name(original)
    if formal.casefold() == symbol.casefold():
        formal = f"{formal} Unit"
    whole = getattr(doc, "must_be_whole_number", None)
    decimal_places = getattr(doc, "decimal_places", None)
    if decimal_places is not None:
        try:
            decimal_places = max(0, min(4, int(decimal_places)))
        except (TypeError, ValueError):
            decimal_places = None
    if decimal_places is None:
        decimal_places = 0 if whole else 4
    return {
        "source_name": original,
        "symbol": symbol,
        "formal_name": formal,
        "uqc": _uom_uqc(original),
        "decimal_places": decimal_places,
    }


def tally_uom_name(doc):
    return _uom_definition(doc)["symbol"]


def _identity_tally_name(connection_name, source_doctype, source_name):
    if not connection_name:
        return None
    return frappe.db.get_value(
        "Tally Master Identity",
        {
            "connection": connection_name,
            "source_doctype": source_doctype,
            "source_name": source_name,
        },
        "tally_name",
    )



def _item_hsn_code(doc):
    """Resolve the ERPNext HSN/SAC value without inventing a tax code."""
    for field in ("gst_hsn_code", "hsn_code", "custom_hsn_code", "gst_hsn"):
        value = getattr(doc, field, None)
        if value:
            return str(value).strip()
    return None


def _item_hsn_description(doc, hsn_code):
    for field in ("gst_hsn_description", "hsn_description", "custom_hsn_description"):
        value = getattr(doc, field, None)
        if value:
            return str(value).strip()

    if not hsn_code:
        return None

    try:
        if frappe.db.exists("DocType", "GST HSN Code"):
            return (
                frappe.db.get_value("GST HSN Code", hsn_code, "description")
                or frappe.db.get_value("GST HSN Code", hsn_code, "hsn_description")
            )
    except Exception:
        pass
    return None


def _classify_tax_head(value):
    text = str(value or "").strip().casefold()
    if "igst" in text:
        return "IGST"
    if "cgst" in text:
        return "CGST"
    if "sgst" in text or "utgst" in text:
        return "SGST/UTGST"
    if "cess" in text:
        return "Cess"
    return None


def _item_gst_rates(doc):
    """Resolve GST component rates from the ERPNext Item Tax Template.

    We deliberately do not infer a GST slab from the HSN code. The ERPNext
    item tax configuration is the source of truth for the rate.
    """
    rates = {}

    direct_fields = {
        "IGST": ("igst_rate", "gst_igst_rate"),
        "CGST": ("cgst_rate", "gst_cgst_rate"),
        "SGST/UTGST": ("sgst_rate", "gst_sgst_rate", "utgst_rate"),
        "Cess": ("cess_rate", "gst_cess_rate"),
    }
    for head, fields in direct_fields.items():
        for field in fields:
            value = getattr(doc, field, None)
            if value not in (None, ""):
                try:
                    rates[head] = float(value)
                    break
                except (TypeError, ValueError):
                    pass

    for assignment in doc.get("taxes") or []:
        template_name = getattr(assignment, "item_tax_template", None)
        if not template_name:
            continue
        try:
            template = frappe.get_cached_doc("Item Tax Template", template_name)
        except Exception:
            continue

        for row in template.get("taxes") or []:
            account = (
                getattr(row, "tax_type", None)
                or getattr(row, "tax", None)
                or getattr(row, "account_head", None)
            )
            head = _classify_tax_head(account)
            if not head:
                continue
            rate = getattr(row, "tax_rate", None)
            if rate in (None, ""):
                continue
            try:
                rates[head] = float(rate)
            except (TypeError, ValueError):
                continue

    if "IGST" not in rates and rates.get("CGST") is not None and rates.get("SGST/UTGST") is not None:
        if abs(rates["CGST"] - rates["SGST/UTGST"]) < 1e-9:
            rates["IGST"] = rates["CGST"] + rates["SGST/UTGST"]

    return rates


def _add_item_gst_details(stock_item, doc):
    """Render Tally Stock Item GST + HSN master details."""
    hsn_code = _item_hsn_code(doc)
    hsn_description = _item_hsn_description(doc, hsn_code)
    rates = _item_gst_rates(doc)

    if not hsn_code and not rates:
        return

    _text(stock_item, "GSTAPPLICABLE", "Applicable")
    _text(stock_item, "GSTTYPEOFSUPPLY", "Goods")

    gst = SubElement(stock_item, "GSTDETAILS.LIST")
    _text(gst, "APPLICABLEFROM", _effective_date(doc))
    _text(gst, "TAXABILITY", "Taxable")
    _text(gst, "SRCOFGSTDETAILS", "Specify Details Here")
    _text(gst, "GSTCALCSLABONMRP", "No")
    _text(gst, "ISREVERSECHARGEAPPLICABLE", "No")
    _text(gst, "ISNONGSTGOODS", "No")
    _text(gst, "GSTINELIGIBLEITC", "No")
    _text(gst, "INCLUDEEXPFORSLABCALC", "No")
    _text(gst, "ISTAXONMRP", "No")

    statewise = SubElement(gst, "STATEWISEDETAILS.LIST")
    _text(statewise, "STATENAME", "Any")

    for head in ("CGST", "SGST/UTGST", "IGST", "Cess"):
        if head not in rates:
            continue
        detail = SubElement(statewise, "RATEDETAILS.LIST")
        _text(detail, "GSTRATEDUTYHEAD", head)
        _text(detail, "GSTRATEVALUATIONTYPE", "Based on Value")
        _text(detail, "GSTRATE", rates[head])

    _text(gst, "GSTSLABRATES.LIST", "")
    _text(gst, "TEMPGSTITEMSLABRATES.LIST", "")
    _text(gst, "TEMPGSTDETAILSLABRATES.LIST", "")

    if hsn_code:
        hsn = SubElement(stock_item, "HSNDETAILS.LIST")
        _text(hsn, "APPLICABLEFROM", _effective_date(doc))
        _text(hsn, "HSNCODE", hsn_code)
        if hsn_description:
            _text(hsn, "HSN", hsn_description)
        _text(hsn, "SRCOFHSNDETAILS", "Specify Details Here")

def item(doc, company: str, action="Create", tally_name: str | None = None) -> str:
    display_name = getattr(doc, "item_name", None) or doc.name
    target_name = tally_name or display_name
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    stock_item = SubElement(m, "STOCKITEM", {"NAME": target_name, "ACTION": action.upper()})
    _text(stock_item, "NAME", display_name)
    names = SubElement(stock_item, "NAME.LIST", {"TYPE": "String"})
    _text(names, "NAME", display_name)
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    _text(names, "NAME", remote)

    stock_uom = getattr(doc, "stock_uom", None)
    if stock_uom:
        # Prefer the persisted ERPNext -> Tally identity. During initial
        # provisioning, fall back to the same deterministic symbol used by
        # the UOM renderer, so the Item can reference the Unit correctly.
        tally_uom = _identity_tally_name(
            getattr(frappe.flags, "tally_bridge_connection", None),
            "UOM",
            stock_uom,
        )
        if not tally_uom:
            tally_uom = _uom_symbol(stock_uom)
        _text(stock_item, "BASEUNITS", tally_uom)

    # Item GST/HSN is part of the Tally Stock Item master itself. Without
    # these lists, Tally creates the item but leaves statutory details blank.
    _add_item_gst_details(stock_item, doc)

    return _envelope([m], company, remote)


def uom(doc, company: str, action="Create", tally_name: str | None = None) -> str:
    definition = _uom_definition(doc)
    target_name = tally_name or definition["symbol"]

    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    unit = SubElement(m, "UNIT", {"NAME": target_name, "ACTION": action.upper()})

    # Tally treats NAME as the unit Symbol. It is also required inside the
    # UNIT object; sending only the XML attribute causes "Master name is missing".
    _text(unit, "NAME", definition["symbol"])
    _text(unit, "ISSIMPLEUNIT", "Yes")
    _text(unit, "ORIGINALNAME", definition["formal_name"])
    _text(unit, "DECIMALPLACES", definition["decimal_places"])

    # TallyPrime's GST/UQC fields are represented by GSTREPUOM. For Release
    # 3.x+ also write Reporting UQC history, matching Tally's current schema.
    _text(unit, "GSTREPUOM", definition["uqc"])
    reporting = SubElement(unit, "REPORTINGUQCDETAILS.LIST")
    _text(reporting, "APPLICABLEFROM", _effective_date(doc))
    _text(reporting, "REPORTINGUQCNAME", definition["uqc"])

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


def account(doc, company: str, action="Create", tally_name: str | None = None) -> str:
    display_name = getattr(doc, "account_name", None) or doc.name
    target_name = tally_name or display_name
    m = Element("TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    ledger = SubElement(m, "LEDGER", {"NAME": target_name, "ACTION": action.upper()})
    _text(ledger, "NAME", display_name)
    _text(ledger, "PARENT", _account_parent(doc))
    remote = stable_remote_id(frappe.local.site, doc.doctype, doc.name)
    return _envelope([m], company, remote)



def render_master(doc, company: str, action="Create", tally_name: str | None = None) -> str:
    if doc.doctype == "Customer":
        return customer(doc, company, action, tally_name)
    if doc.doctype == "Supplier":
        return supplier(doc, company, action, tally_name)
    if doc.doctype == "Item":
        return item(doc, company, action, tally_name)
    if doc.doctype == "UOM":
        return uom(doc, company, action, tally_name)
    if doc.doctype == "Account":
        return account(doc, company, action, tally_name)
    frappe.throw(f"No master renderer registered for {doc.doctype}")
