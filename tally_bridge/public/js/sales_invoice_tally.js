// tally_bridge/public/js/sales_invoice_tally.js
// Adds "Export to Tally" button on the Sales Invoice form

frappe.ui.form.on("Sales Invoice", {
    refresh(frm) {
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Export to Tally"), function () {
                frappe.confirm(
                    `Export <b>${frm.doc.name}</b> to Tally Prime?<br>
                     <small>This will push the sales voucher to Tally if the connection is configured.</small>`,
                    function () {
                        frappe.call({
                            method: "tally_bridge.api.export.export_single_document",
                            args: {
                                doctype: "Sales Invoice",
                                docname: frm.doc.name,
                                push_to_tally_flag: true,
                            },
                            freeze: true,
                            freeze_message: __("Exporting to Tally…"),
                            callback(r) {
                                const d = r.message;
                                if (d && d.success) {
                                    const tally = d.tally;
                                    const msg = tally
                                        ? `Pushed to Tally: ${tally.created} created, ${tally.altered} altered`
                                        : `XML generated. Log: ${d.log}`;
                                    frappe.show_alert({ message: msg, indicator: "green" });
                                } else {
                                    frappe.msgprint({
                                        title: "Export Failed",
                                        message: (d && d.error) || "Unknown error",
                                        indicator: "red",
                                    });
                                }
                            },
                        });
                    }
                );
            }, __("Tally"));

            frm.add_custom_button(__("Download XML"), function () {
                frappe.call({
                    method: "tally_bridge.api.export.export_single_document",
                    args: {
                        doctype: "Sales Invoice",
                        docname: frm.doc.name,
                        push_to_tally_flag: false,
                    },
                    callback(r) {
                        const d = r.message;
                        if (d && d.success && d.xml) {
                            const blob = new Blob([d.xml], { type: "application/xml" });
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement("a");
                            a.href = url;
                            a.download = `${frm.doc.name}_tally.xml`;
                            a.click();
                            URL.revokeObjectURL(url);
                        }
                    },
                });
            }, __("Tally"));
        }
    },
});
