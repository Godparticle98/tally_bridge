import frappe

def run():
    print("Checking Workspace...")
    if not frappe.db.exists("Workspace", "Tally Bridge"):
        print("Creating workspace...")
        ws = frappe.new_doc("Workspace")
        ws.title = "Tally Bridge"
        ws.name = "Tally Bridge"
        ws.label = "Tally Bridge"
        ws.module = "Tally Bridge"
        ws.is_standard = 1
        ws.app = "tally_bridge"
        ws.public = 1
        
        if hasattr(ws, 'content'):
            ws.content = '[{"id": "header", "type": "header", "data": {"text": "Tally Tools", "level": 3}}]'
        
        ws.insert(ignore_permissions=True)
        print("Base Workspace inserted!")
        
        if frappe.get_meta("Workspace Shortcut").get_field("link_type"):
             try:
                 shortcut = frappe.new_doc("Workspace Shortcut")
                 shortcut.label = "Tally Settings"
                 shortcut.type = "DocType"
                 shortcut.link_type = "DocType"
                 shortcut.parent = "Tally Bridge"
                 shortcut.link_to = "Tally Settings"
                 shortcut.insert(ignore_permissions=True)
             except Exception:
                 pass
                 
             try:
                 shortcut = frappe.new_doc("Workspace Shortcut")
                 shortcut.label = "Export Logs"
                 shortcut.type = "DocType"
                 shortcut.link_type = "DocType"
                 shortcut.parent = "Tally Bridge"
                 shortcut.link_to = "Tally Export Log"
                 shortcut.insert(ignore_permissions=True)
             except Exception:
                 pass
        
    print("Done")
