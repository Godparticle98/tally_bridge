import frappe
def run():
    print("Modules for tally_bridge:", frappe.get_module_list("tally_bridge"))
    import os
    print("Path to modules.txt:", os.path.join(frappe.get_app_path("tally_bridge"), "modules.txt"))
    print("modules.txt exists:", os.path.exists(os.path.join(frappe.get_app_path("tally_bridge"), "modules.txt")))
