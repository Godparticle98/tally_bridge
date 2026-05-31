import frappe

def get_context(context):
    # Ensure user is logged in
    if frappe.session.user == "Guest":
        frappe.throw(frappe._("You must be logged in to access this page"), frappe.PermissionError)

    # Restrict to System Managers
    if "System Manager" not in frappe.get_roles():
        frappe.throw(frappe._("Not allowed. You need System Manager role."), frappe.PermissionError)
