// tally_bridge/public/js/purchase_invoice_tally.js

frappe.ui.form.on("Purchase Invoice", {
    refresh(frm) {
        if (frm.doc.docstatus === 1) {
            _add_tally_buttons(frm, "Purchase Invoice");
        }
    },
});

// ─────────────────────────────────────────────────────────────
// tally_bridge/public/js/payment_entry_tally.js (combined)

frappe.ui.form.on("Payment Entry", {
    refresh(frm) {
        if (frm.doc.docstatus === 1) {
            _add_tally_buttons(frm, "Payment Entry");
        }
    },
});

// ─────────────────────────────────────────────────────────────
// tally_bridge/public/js/journal_entry_tally.js (combined)

frappe.ui.form.on("Journal Entry", {
    refresh(frm) {
        if (frm.doc.docstatus === 1) {
            _add_tally_buttons(frm, "Journal Entry");
        }
    },
});

// ─────────────────────────────────────────────────────────────
// Shared helper — adds Export + Download buttons

function _add_tally_buttons(frm, doctype) {
    frm.add_custom_button(__("Export to Tally"), function () {
        frappe.confirm(
            `Push <b>${frm.doc.name}</b> to Tally Prime?`,
            function () {
                frappe.call({
                    method: "tally_bridge.api.export.export_single_document",
                    args: { doctype, docname: frm.doc.name, push_to_tally_flag: true },
                    freeze: true,
                    freeze_message: __("Pushing to Tally…"),
                    callback(r) {
                        const d = r.message;
                        if (d && d.success) {
                            frappe.show_alert({
                                message: d.tally
                                    ? `Tally: ${d.tally.created} created`
                                    : "XML generated: " + d.log,
                                indicator: "green"
                            });
                        } else {
                            frappe.msgprint({ title: "Export Failed",
                                message: (d && d.error) || "Unknown error", indicator: "red" });
                        }
                    }
                });
            }
        );
    }, __("Tally"));

    frm.add_custom_button(__("Download XML"), function () {
        frappe.call({
            method: "tally_bridge.api.export.export_single_document",
            args: { doctype, docname: frm.doc.name, push_to_tally_flag: false },
            callback(r) {
                const d = r.message;
                if (d && d.success && d.xml) {
                    const blob = new Blob([d.xml], { type: "application/xml" });
                    const a = Object.assign(document.createElement("a"), {
                        href: URL.createObjectURL(blob),
                        download: `${frm.doc.name}_tally.xml`
                    });
                    a.click();
                }
            }
        });
    }, __("Tally"));
}
