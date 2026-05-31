app_name = "tally_bridge"
app_title = "Tally Bridge"
app_publisher = "Manoj Seenivasan"
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
    "Sales Invoice":    "tally_bridge/doctype/overrides/sales_invoice_tally.js",
    "Purchase Invoice": "tally_bridge/doctype/overrides/purchase_invoice_tally.js",
    "Payment Entry":    "tally_bridge/doctype/overrides/payment_entry_tally.js",
    "Journal Entry":    "tally_bridge/doctype/overrides/journal_entry_tally.js",
}

# NOTE: app_include_js / app_include_css are intentionally omitted.
# Those keys require a compiled bundle under assets/tally_bridge/
# which needs an esbuild entry point. Since this app has no custom
# bundle, removing them prevents the ERR_INVALID_ARG_TYPE build error.

website_route_rules = [{'from_route': '/tally-bridge/<path:app_path>', 'to_route': 'tally_bridge'}, {'from_route': '/tally-bridge', 'to_route': 'tally_bridge'}]
