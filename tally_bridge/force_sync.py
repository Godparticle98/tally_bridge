import frappe
from frappe.model.sync import sync_for

def run():
    print("Forcing sync for tally_bridge...")
    try:
        sync_for("tally_bridge", force=True)
        print("Sync completed!")
    except Exception as e:
        print("Error during sync:", e)
        import traceback
        traceback.print_exc()

    print("Module Tally Bridge after sync:", frappe.db.exists("Module Def", "Tally Bridge"))
    print("Doctype Tally Settings after sync:", frappe.db.exists("DocType", "Tally Settings"))
