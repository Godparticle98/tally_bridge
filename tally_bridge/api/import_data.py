import frappe
from frappe import _
import xml.etree.ElementTree as ET
import traceback

@frappe.whitelist()
def process_tally_xml(xml_data):
    """
    Parses Tally XML and imports/updates Masters and Bank Reconciliation dates in ERPNext.
    """
    if not xml_data:
        return {"success": False, "error": "No XML data provided"}

    try:
        import re
        
        # Tally XML often contains non-standard characters, but standard ET can usually parse it
        # Strip out any potential BOM or leading whitespace
        xml_data = xml_data.strip()
        
        # Remove conflicting xml declaration
        xml_data = re.sub(r'<\?xml[^>]+\?>', '', xml_data)
        
        # Remove invalid entity references like &#x1A;
        def clean_entities(match):
            val = match.group(1).lower()
            try:
                num = int(val[1:], 16) if val.startswith('x') else int(val)
                if num in (0x9, 0xA, 0xD) or (0x20 <= num <= 0xD7FF) or (0xE000 <= num <= 0xFFFD):
                    return match.group(0)
            except:
                pass
            return ''
            
        xml_data = re.sub(r'&#([xX]?[0-9a-fA-F]+);', clean_entities, xml_data)
        
        # Remove literal invalid characters
        xml_data = re.sub(r'[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]', '', xml_data)

        root = ET.fromstring(xml_data.encode("utf-8"))
        
        settings = frappe.get_single("Tally Settings")
        debtors_group = settings.get("sundry_debtors_ledger") or "Sundry Debtors"
        creditors_group = settings.get("sundry_creditors_ledger") or "Sundry Creditors"
        company = settings.get("company")
        bank_recon_master = settings.get("bank_recon_master") or "ERPNext"

        if not company:
            return {"success": False, "error": "Company not set in Tally Settings."}

        counts = {
            "customers": 0,
            "suppliers": 0,
            "accounts": 0,
            "items": 0,
            "bank_allocations": 0
        }

        # Find all TALLYMESSAGE nodes
        # Tally wraps data in <ENVELOPE><BODY><DATA><TALLYMESSAGE>
        messages = root.findall(".//TALLYMESSAGE")
        if not messages:
            # Maybe it's a direct XML without envelope
            if root.tag == "TALLYMESSAGE":
                messages = [root]

        for msg in messages:
            # 1. Ledgers (Accounts, Customers, Suppliers)
            ledger = msg.find("LEDGER")
            if ledger is not None:
                ledger_name = ledger.get("NAME")
                parent_group = ledger.findtext("PARENT") or ""
                
                # Check if it's a Customer
                if parent_group.lower() == debtors_group.lower():
                    if not frappe.db.exists("Customer", ledger_name):
                        customer = frappe.new_doc("Customer")
                        customer.customer_name = ledger_name
                        customer.customer_group = "All Customer Groups"
                        customer.territory = "All Territories"
                        customer.customer_type = "Company"
                        customer.insert(ignore_permissions=True)
                        counts["customers"] += 1
                
                # Check if it's a Supplier
                elif parent_group.lower() == creditors_group.lower():
                    if not frappe.db.exists("Supplier", ledger_name):
                        supplier = frappe.new_doc("Supplier")
                        supplier.supplier_name = ledger_name
                        supplier.supplier_group = "All Supplier Groups"
                        supplier.supplier_type = "Company"
                        supplier.insert(ignore_permissions=True)
                        counts["suppliers"] += 1
                
                # Otherwise, it's a General Account
                else:
                    account_name = f"{ledger_name} - {frappe.db.get_value('Company', company, 'abbr')}"
                    if not frappe.db.exists("Account", account_name):
                        # We don't have the full tree, so we place it under a generic group or try to map
                        # This is complex in real life, so we log it or create under a specific parent.
                        # For now, we will just count it to avoid breaking the Chart of Accounts hierarchy.
                        counts["accounts"] += 1

            # 2. Stock Items
            item = msg.find("STOCKITEM")
            if item is not None:
                item_name = item.get("NAME")
                uom = item.findtext("BASEUNITS") or "Nos"
                
                if not frappe.db.exists("Item", item_name):
                    # Create UOM if not exists
                    if uom and not frappe.db.exists("UOM", uom):
                        frappe.get_doc({"doctype": "UOM", "uom_name": uom}).insert(ignore_permissions=True)
                        
                    new_item = frappe.new_doc("Item")
                    new_item.item_code = item_name
                    new_item.item_name = item_name
                    new_item.item_group = "All Item Groups"
                    new_item.stock_uom = uom
                    new_item.is_stock_item = 1
                    new_item.insert(ignore_permissions=True)
                    counts["items"] += 1

            # 3. Bank Reconciliation Dates (Phase 3)
            voucher = msg.find("VOUCHER")
            if voucher is not None and bank_recon_master == "Tally":
                vch_number = voucher.findtext("VOUCHERNUMBER")
                if vch_number:
                    bank_allocs = voucher.findall(".//BANKALLOCATIONS.LIST")
                    for alloc in bank_allocs:
                        clearance_date_str = alloc.findtext("BANKERSDATE")
                        if clearance_date_str and len(clearance_date_str) >= 8:
                            try:
                                c_date = f"{clearance_date_str[:4]}-{clearance_date_str[4:6]}-{clearance_date_str[6:8]}"
                                
                                # Try matching Payment Entry first
                                if frappe.db.exists("Payment Entry", vch_number):
                                    doc = frappe.get_doc("Payment Entry", vch_number)
                                    if not doc.clearance_date or str(doc.clearance_date) != c_date:
                                        doc.db_set("clearance_date", c_date)
                                        counts["bank_allocations"] += 1
                                # Try Journal Entry
                                elif frappe.db.exists("Journal Entry", vch_number):
                                    doc = frappe.get_doc("Journal Entry", vch_number)
                                    if not doc.clearance_date or str(doc.clearance_date) != c_date:
                                        doc.db_set("clearance_date", c_date)
                                        counts["bank_allocations"] += 1
                            except Exception as e:
                                frappe.logger().error(f"Failed to reconcile voucher {vch_number}: {str(e)}")

        frappe.db.commit()

        msg_parts = []
        if counts["customers"]: msg_parts.append(f"{counts['customers']} Customers")
        if counts["suppliers"]: msg_parts.append(f"{counts['suppliers']} Suppliers")
        if counts["items"]: msg_parts.append(f"{counts['items']} Items")
        if counts["bank_allocations"]: msg_parts.append(f"{counts['bank_allocations']} Bank Reconciliations")
        
        if not msg_parts:
            return {"success": True, "message": "Parsed XML, but no new records were created or updated."}
            
        return {"success": True, "message": "Created/Updated: " + ", ".join(msg_parts)}

    except Exception as e:
        frappe.log_error(title="Tally XML Import Error", message=traceback.format_exc())
        return {"success": False, "error": f"Error parsing XML: {str(e)}"}
