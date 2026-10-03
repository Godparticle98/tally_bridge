frappe.ui.form.on("Tally Export Job", {
    refresh(frm) {
        if (frm.is_new()) {
            return;
        }

        if (frm.doc.status !== "Processing") {
            frm.add_custom_button(__("Run Export"), () => {
                const run = () => {
                    frappe.call({
                        method: "tally_bridge.api.queue_export_job",
                        args: { job_name: frm.doc.name },
                        freeze: true,
                        freeze_message: __("Queueing Tally export...")
                    }).then((r) => {
                        if (!r.exc) {
                            frappe.show_alert({
                                message: __("Export queued. The ZIP will appear in Output File when complete."),
                                indicator: "blue"
                            });
                            frm.reload_doc();
                        }
                    });
                };

                if (frm.is_dirty()) {
                    frm.save().then(run);
                } else {
                    run();
                }
            }).addClass("btn-primary");
        }

        if (frm.doc.status === "Completed" && frm.doc.output_file) {
            frm.add_custom_button(__("Open Export"), () => {
                window.open(frm.doc.output_file, "_blank");
            });
        }
    }
});
