// catering_production_plan.js
frappe.ui.form.on("Catering Production Plan", {
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Create Work Orders"), () => {
				frappe.confirm(__("Create Work Orders for all items with a BOM?"), () => {
					(frm.doc.items || []).forEach(item => {
						if (item.bom && !item.work_order) {
							frappe.call({
								method: "frappe.client.insert",
								args: { doc: {
									doctype: "Work Order",
									production_item: item.item_code,
									bom_no: item.bom,
									qty: item.planned_qty,
									company: frm.doc.company,
									catering_order: frm.doc.catering_order,
									planned_start_date: frm.doc.planned_start_date,
								}},
								callback(r) {
									if (r.message) {
										frappe.model.set_value(item.doctype, item.name, "work_order", r.message.name);
										frappe.msgprint(__("Work Order {0} created.", [r.message.name]));
									}
								}
							});
						}
					});
				});
			});
		}
	},
	catering_order(frm) {
		if (!frm.doc.catering_order) return;
		frappe.db.get_value("Catering Order", frm.doc.catering_order,
			["customer_name","event_date","total_guests","company"], v => {
			frm.set_value("customer", v.customer_name);
			frm.set_value("event_date", v.event_date);
			frm.set_value("total_guests", v.total_guests);
			if (!frm.doc.company) frm.set_value("company", v.company);
		});
	},
});

frappe.ui.form.on("Catering Production Plan Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.db.get_value("Item", row.item_code, ["item_name","stock_uom"], v => {
			frappe.model.set_value(cdt, cdn, "item_name", v.item_name);
			frappe.model.set_value(cdt, cdn, "uom", v.stock_uom);
		});
		// Auto-find default BOM
		frappe.db.get_value("BOM", {item: row.item_code, is_active: 1, is_default: 1}, "name", v => {
			if (v && v.name) frappe.model.set_value(cdt, cdn, "bom", v.name);
		});
	},
});
