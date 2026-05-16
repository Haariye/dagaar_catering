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
			frm.add_custom_button(__("Create Delivery Note"), () => {
				const dn = frappe.new_doc("Delivery Note");
				dn.customer = frm.doc.customer;
				dn.company = frm.doc.company;
				dn.catering_order = frm.doc.catering_order;
			});
		}
	},
	catering_order(frm) {
		if (!frm.doc.catering_order) return;
		frappe.db.get_value("Catering Order", frm.doc.catering_order,
			["customer_name","event_date","event_location","company"], v => {
			frm.set_value("customer", v.customer_name);
			frm.set_value("delivery_date", v.event_date);
			frm.set_value("delivery_address", v.event_location);
			if (!frm.doc.company) frm.set_value("company", v.company);
		});
	},
});

frappe.ui.form.on("Catering Delivery Plan Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.db.get_value("Item", row.item_code, ["item_name","stock_uom"], v => {
			frappe.model.set_value(cdt, cdn, "item_name", v.item_name);
			frappe.model.set_value(cdt, cdn, "uom", v.stock_uom);
		});
	},
});
