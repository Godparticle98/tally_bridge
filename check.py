import sys
import os

# add frappe-bench to path so we can import frappe
sys.path.insert(0, '/home/gp/frappe_env/frappe-bench/apps/frappe')
sys.path.insert(0, '/home/gp/frappe_env/frappe-bench/env/lib/python3.14/site-packages')

import frappe
frappe.init(site='erp.draftdu.com', sites_path='/home/gp/frappe_env/frappe-bench/sites')
frappe.connect()

print("--- TEST OUTPUT START ---")
print("Module Tally Bridge exists:", bool(frappe.db.exists("Module Def", "Tally Bridge")))
print("Workspace Tally Bridge exists:", bool(frappe.db.exists("Workspace", "Tally Bridge")))
print("Doctype Tally Settings exists:", bool(frappe.db.exists("DocType", "Tally Settings")))
# Try to find exactly what it's named if it differs
modules = frappe.db.get_list('Module Def', filters={'app_name': 'tally_bridge'}, pluck='name')
print("Modules for tally_bridge app:", modules)
print("--- TEST OUTPUT END ---")
