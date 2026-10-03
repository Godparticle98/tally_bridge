import frappe
from frappe.model.document import Document
from frappe.utils import getdate, now_datetime


class TallyExportJob(Document):
    def validate(self):
        if self.from_date and self.to_date and getdate(self.from_date) > getdate(self.to_date):
            frappe.throw("From Date cannot be later than To Date.")

        if self.format != "XML":
            frappe.throw("Only XML export is currently supported.")

        if self.transaction_types:
            supported = {
                "Sales Invoice",
                "Purchase Invoice",
                "Payment Entry",
                "Journal Entry",
            }
            requested = {value.strip() for value in self.transaction_types.split(",") if value.strip()}
            unsupported = requested.difference(supported)
            if unsupported:
                frappe.throw(
                    "Unsupported transaction type(s): {0}".format(", ".join(sorted(unsupported)))
                )

    @frappe.whitelist()
    def queue_export(self):
        if not frappe.has_permission("Tally Export Job", "write", self.name):
            frappe.throw("You do not have permission to run this export.", frappe.PermissionError)

        self.check_permission("write")
        self.run_method("validate")

        if self.status == "Processing":
            frappe.throw("This export is already processing.")

        self.status = "Queued"
        self.started_at = None
        self.completed_at = None
        self.error_message = None
        self.output_file = None
        self.save()
        frappe.db.commit()

        frappe.enqueue(
            "tally_bridge.sync.exporter.generate_period_export",
            queue="long",
            kwargs={"job_name": self.name},
            enqueue_after_commit=True,
        )
        return {"job": self.name, "status": "Queued"}
