// catering_production_plan.js
frappe.ui.form.on("Catering Production Plan", {
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			// Bulk-create Work Orders from production plan items
			frm.add_custom_button(__("Create Work Orders"), () => {
				frappe.call({
					method: "dagaar_catering.catering_management.controllers.catering_order.create_work_orders_from_plan",
					args: { production_plan: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating Work Orders..."),
					callback: (r) => {
						if (r.message && r.message.length) {
							frappe.show_alert({
								message: __("Created {0} Work Orders", [r.message.length]),
								indicator: "green"
							}, 6);
							// Open each Work Order in a new tab
							r.message.forEach(wo => {
								window.open(`/app/work-order/${encodeURIComponent(wo)}`, "_blank");
							});
							frm.reload_doc();
						}
					}
				});
			});
		}
	},
});

frappe.ui.form.on("Catering Production Plan Item", {
	work_order(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.work_order) {
			// When clicking the work order link, open in new tab
			const url = `/app/work-order/${encodeURIComponent(row.work_order)}`;
			window.open(url, "_blank");
		}
	},
});
