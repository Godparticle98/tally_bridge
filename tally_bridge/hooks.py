app_name = "tally_bridge"
app_title = "Tally Bridge"
app_publisher = "Draftdu Technologies"
app_description = "Configurable ERPNext to TallyPrime integration with live sync and period export."
app_email = "info@draftdu.example"
app_license = "MIT"
app_version = "0.1.0"

required_apps = ["erpnext"]

# These are deliberately limited to the first supported integration set.
# Additional doctypes can be enabled through Tally DocType Mapping without editing core ERPNext.
doc_events = {
    "Customer": {
        "after_insert": "tally_bridge.sync.enqueue.on_document_event",
        "on_update": "tally_bridge.sync.enqueue.on_document_event",
    },
    "Supplier": {
        "after_insert": "tally_bridge.sync.enqueue.on_document_event",
        "on_update": "tally_bridge.sync.enqueue.on_document_event",
    },
    "Item": {"after_insert": "tally_bridge.sync.enqueue.on_document_event"},
    "UOM": {"after_insert": "tally_bridge.sync.enqueue.on_document_event"},
    "Account": {"after_insert": "tally_bridge.sync.enqueue.on_document_event"},
    "Sales Invoice": {
        "on_submit": "tally_bridge.sync.enqueue.on_document_event",
        "on_cancel": "tally_bridge.sync.enqueue.on_document_event",
        "on_update_after_submit": "tally_bridge.sync.enqueue.on_document_event",
    },
    "Purchase Invoice": {
        "on_submit": "tally_bridge.sync.enqueue.on_document_event",
        "on_cancel": "tally_bridge.sync.enqueue.on_document_event",
        "on_update_after_submit": "tally_bridge.sync.enqueue.on_document_event",
    },
    "Payment Entry": {
        "on_submit": "tally_bridge.sync.enqueue.on_document_event",
        "on_cancel": "tally_bridge.sync.enqueue.on_document_event",
    },
    "Journal Entry": {
        "on_submit": "tally_bridge.sync.enqueue.on_document_event",
        "on_cancel": "tally_bridge.sync.enqueue.on_document_event",
    },
}

after_sync = "tally_bridge.sync.service.after_sync"
