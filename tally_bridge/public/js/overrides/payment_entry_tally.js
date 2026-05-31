// tally_bridge/public/js/payment_entry_tally.js
// Adds "Export to Tally" and "Download XML" buttons on submitted Payment Entries

frappe.ui.form.on("Payment Entry", {
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Export to Tally"), function () {
				frappe.confirm(
					`Push <b>${frm.doc.name}</b> to Tally Prime?`,
					function () {
						frappe.call({
							method: "tally_bridge.api.export.export_single_document",
							args: {
								doctype: "Payment Entry",
								docname: frm.doc.name,
								push_to_tally_flag: true,
							},
							freeze: true,
							freeze_message: __("Pushing to Tally…"),
							callback(r) {
								const d = r.message;
								if (d && d.success) {
									frappe.show_alert({
										message: d.tally
											? `Tally: ${d.tally.created} created, ${d.tally.altered} altered`
											: `XML generated. Log: ${d.log}`,
										indicator: "green",
									});
								} else {
									frappe.msgprint({
										title: __("Export Failed"),
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
						doctype: "Payment Entry",
						docname: frm.doc.name,
						push_to_tally_flag: false,
					},
					callback(r) {
						const d = r.message;
						if (d && d.success && d.xml) {
							const blob = new Blob([d.xml], { type: "application/xml" });
							const a = Object.assign(document.createElement("a"), {
								href: URL.createObjectURL(blob),
								download: `${frm.doc.name}_tally.xml`,
							});
							a.click();
							URL.revokeObjectURL(a.href);
						}
					},
				});
			}, __("Tally"));
		}
	},
});
