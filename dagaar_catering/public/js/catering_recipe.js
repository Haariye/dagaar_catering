// catering_recipe.js
frappe.ui.form.on("Catering Recipe", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Create BOM"), () => {
				frappe.call({
					method: "frappe.client.get_list",
					args: { doctype: "BOM", filters: { item: frm.doc.item_code, is_active: 1 }, fields: ["name"] },
					callback(r) {
						if (r.message && r.message.length) {
							frappe.confirm(__("BOM already exists. Open existing BOM?"), () => {
								frappe.set_route("Form", "BOM", r.message[0].name);
							});
						} else {
							frappe.new_doc("BOM", { item: frm.doc.item_code, quantity: frm.doc.yield_qty });
						}
					}
				});
			});
		}
		frm.trigger("calculate_cost");
	},
	calculate_cost(frm) {
		let total = 0;
		(frm.doc.ingredients || []).forEach(i => {
			i.amount = flt(i.qty) * flt(i.cost_per_unit);
			total += i.amount;
		});
		frm.set_value("total_ingredient_cost", total);
		const yield_qty = flt(frm.doc.yield_qty) || 1;
		frm.set_value("cost_per_serving", total / yield_qty);
		frm.refresh_field("ingredients");
	},
});

frappe.ui.form.on("Catering Recipe Ingredient", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.db.get_value("Item", row.item_code, ["item_name", "stock_uom", "standard_rate"], v => {
			frappe.model.set_value(cdt, cdn, "item_name", v.item_name);
			frappe.model.set_value(cdt, cdn, "uom", v.stock_uom);
			frappe.model.set_value(cdt, cdn, "cost_per_unit", v.standard_rate || 0);
			frappe.model.set_value(cdt, cdn, "amount", flt(row.qty) * flt(v.standard_rate));
			frm.trigger("calculate_cost");
		});
	},
	qty(frm) { frm.trigger("calculate_cost"); },
	cost_per_unit(frm) { frm.trigger("calculate_cost"); },
	ingredients_remove(frm) { frm.trigger("calculate_cost"); },
});
