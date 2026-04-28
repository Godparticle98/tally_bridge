app_name = "tally_bridge"
app_title = "Tally Bridge"
app_publisher = "Your Company"
app_description = "ERPNext to Tally Prime XML Export & Sync"
app_email = "admin@example.com"
app_license = "MIT"
app_version = "1.0.0"

# ──────────────────────────────────────────────
# Scheduled tasks
# ──────────────────────────────────────────────
scheduler_events = {
    "hourly": [
        "tally_bridge.api.sync.auto_sync_to_tally"
    ],
    "daily": [
        "tally_bridge.api.sync.daily_full_sync"
    ],
}

# ──────────────────────────────────────────────
# Whitelisted API methods
# ──────────────────────────────────────────────
# (all frappe.whitelist decorated functions are auto-registered)

# ──────────────────────────────────────────────
# DocType JS overrides
# ──────────────────────────────────────────────
doctype_js = {
    "Sales Invoice": "public/js/sales_invoice_tally.js",
    "Purchase Invoice": "public/js/purchase_invoice_tally.js",
    "Payment Entry": "public/js/payment_entry_tally.js",
    "Journal Entry": "public/js/journal_entry_tally.js",
}

# ──────────────────────────────────────────────
# App includes
# ──────────────────────────────────────────────
app_include_js = ["assets/tally_bridge/js/tally_bridge.js"]
app_include_css = ["assets/tally_bridge/css/tally_bridge.css"]
