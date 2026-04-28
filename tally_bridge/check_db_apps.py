import frappe

def run():
    print("Installed Apps:", getattr(frappe, 'get_installed_apps', lambda: 'N/A')())
