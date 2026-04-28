import frappe

def run():
    print("Module:", frappe.db.exists("Module Def", "Tally Bridge"))
    print("Doctype:", frappe.db.exists("DocType", "Tally Settings"))
    print("Workspace:", frappe.db.exists("Workspace", "Tally Bridge"))
