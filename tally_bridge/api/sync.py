"""
tally_bridge/api/sync.py
Scheduled tasks for automatic Tally sync.
"""

import frappe
from frappe.utils import now_datetime, add_days, today, getdate


def auto_sync_to_tally():
    """Hourly scheduler — only runs if auto sync is enabled."""
    settings = frappe.get_single("Tally Settings")
    if not settings.auto_sync_enabled:
        return

    if settings.sync_interval == "Hourly":
        _run_sync()
    elif settings.sync_interval == "Every 6 Hours":
        # Only run if last sync was > 6 hours ago
        if settings.last_sync_at:
            from frappe.utils import time_diff_in_hours
            diff = time_diff_in_hours(now_datetime(), settings.last_sync_at)
            if diff < 6:
                return
        _run_sync()


def daily_full_sync():
    """Daily full sync — runs regardless of auto_sync_enabled for the daily interval."""
    settings = frappe.get_single("Tally Settings")
    if settings.auto_sync_enabled and settings.sync_interval == "Daily":
        _run_sync()


def _run_sync():
    """Run the actual sync for today's data."""
    settings = frappe.get_single("Tally Settings")
    from_date = str(today())
    to_date = str(today())

    try:
        from tally_bridge.api.export import export_all
        result = export_all(
            from_date=from_date,
            to_date=to_date,
            export_format="Tally XML",
            push_to_tally_flag=True,
        )
        frappe.log_error(
            f"Auto sync result: {result.get('records', 0)} records, "
            f"status: {'OK' if result.get('success') else 'FAILED'}",
            "Tally Bridge: Auto Sync"
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Tally Bridge: Auto Sync Failed")
    finally:
        # Update last sync time
        settings.last_sync_at = now_datetime()
        settings.save(ignore_permissions=True)
        frappe.db.commit()
