import frappe
def run():
    import os
    doctype_path = os.path.join(frappe.get_app_path("tally_bridge"), "doctype")
    print("Doctype dir exists:", os.path.exists(doctype_path))
    if os.path.exists(doctype_path):
        print("Contents of doctype:", os.listdir(doctype_path))
