// catering_menu_package.js
frappe.ui.form.on("Catering Menu Package", {
	refresh(frm) {
		frm.add_custom_button(__("Use in New Order"), () => {
			frappe.new_doc("Catering Order", {
				menu_package: frm.doc.name,
				package_type: frm.doc.package_type,
			});
		});
	},
	package_type(frm) { frm.trigger("calculate_price"); },
	calculate_price(frm) {
		let total = 0;
		(frm.doc.items || []).forEach(i => { total += flt(i.qty_per_guest) * flt(i.rate); });
		if (total > 0 && !flt(frm.doc.price_per_guest)) {
			frm.set_value("price_per_guest", total);
		}
	},
});

frappe.ui.form.on("Catering Menu Package Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.db.get_value("Item", row.item_code, ["item_name","stock_uom","standard_rate"], v => {
			frappe.model.set_value(cdt, cdn, "item_name", v.item_name);
			frappe.model.set_value(cdt, cdn, "uom", v.stock_uom);
			if (!row.rate) frappe.model.set_value(cdt, cdn, "rate", v.standard_rate || 0);
		});
	},
});
