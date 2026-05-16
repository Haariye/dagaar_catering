// catering_delivery_plan.js
frappe.ui.form.on("Catering Delivery Plan", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.status !== "Delivered") {
			frm.add_custom_button(__("Mark as Delivered"), () => {
				frappe.confirm(__("Confirm delivery completed?"), () => {
					frappe.db.set_value("Catering Delivery Plan", frm.doc.name, "status", "Delivered").then(() => {
						frappe.db.set_value("Catering Order", frm.doc.catering_order, "status", "Delivered");
						frm.reload_doc();
					});
				});
			});

			// Create Delivery Note — properly opens a NEW Delivery Note in a NEW TAB with all fields pre-filled
			frm.add_custom_button(__("Create Delivery Note"), () => {
				if (!frm.doc.catering_order) {
					frappe.msgprint({
						message: __("This Delivery Plan has no Catering Order linked."),
						indicator: "red"
					});
					return;
				}
				frappe.call({
					method: "dagaar_catering.catering_management.controllers.catering_order.create_delivery_note_from_plan",
					args: { delivery_plan: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating Delivery Note..."),
					callback: (r) => {
						if (r.message) {
							frappe.show_alert({
								message: __("Created Delivery Note {0} — opened in new tab", [r.message]),
								indicator: "green"
							}, 5);
							const url = `/app/delivery-note/${encodeURIComponent(r.message)}`;
							window.open(url, "_blank");
							frm.reload_doc();
						}
					}
				});
			});
		}
	},

	catering_order(frm) {
		if (!frm.doc.catering_order) return;
		frappe.db.get_value("Catering Order", frm.doc.catering_order,
			["customer_name", "event_date", "event_location", "event_address", "company",
			 "contact_mobile", "service_start_time"], v => {
			if (!v) return;
			if (v.customer_name) frm.set_value("customer", v.customer_name);
			if (v.event_date) frm.set_value("delivery_date", v.event_date);
			if (v.event_address || v.event_location) {
				frm.set_value("delivery_address", v.event_address || v.event_location);
			}
			if (v.contact_mobile) frm.set_value("contact_phone", v.contact_mobile);
			if (v.service_start_time) frm.set_value("delivery_time", v.service_start_time);
			if (!frm.doc.company) frm.set_value("company", v.company);
		});
	},
});

frappe.ui.form.on("Catering Delivery Plan Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) return;
		frappe.db.get_value("Item", row.item_code, ["item_name", "stock_uom"], v => {
			if (v) {
				frappe.model.set_value(cdt, cdn, "item_name", v.item_name);
				frappe.model.set_value(cdt, cdn, "uom", v.stock_uom);
			}
		});
	},
});
