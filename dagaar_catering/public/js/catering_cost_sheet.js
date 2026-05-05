// catering_cost_sheet.js
frappe.ui.form.on("Catering Cost Sheet", {
	refresh(frm) { frm.trigger("update_margin_indicator"); },
	catering_order(frm) {
		if (!frm.doc.catering_order) return;
		frappe.db.get_value("Catering Order", frm.doc.catering_order,
			["customer_name","event_date","total_guests","total_order_value","company"], v => {
			frm.set_value("customer", v.customer_name);
			frm.set_value("event_date", v.event_date);
			frm.set_value("total_guests", v.total_guests);
			frm.set_value("total_revenue", v.total_order_value);
			if (!frm.doc.company) frm.set_value("company", v.company);
		});
	},
	food_cost(frm) { frm.trigger("calculate_margin"); },
	beverage_cost(frm) { frm.trigger("calculate_margin"); },
	snacks_cost(frm) { frm.trigger("calculate_margin"); },
	packaging_cost(frm) { frm.trigger("calculate_margin"); },
	labor_cost(frm) { frm.trigger("calculate_margin"); },
	delivery_cost(frm) { frm.trigger("calculate_margin"); },
	rental_cost(frm) { frm.trigger("calculate_margin"); },
	overhead_cost(frm) { frm.trigger("calculate_margin"); },
	calculate_margin(frm) {
		const total = ["food_cost","beverage_cost","snacks_cost","packaging_cost",
					   "labor_cost","delivery_cost","rental_cost","overhead_cost"]
					  .reduce((s,f) => s + flt(frm.doc[f]), 0);
		frm.set_value("total_cost", total);
		const rev = flt(frm.doc.total_revenue);
		const profit = rev - total;
		frm.set_value("gross_profit", profit);
		frm.set_value("gross_margin_percent", rev ? profit / rev * 100 : 0);
		frm.trigger("update_margin_indicator");
	},
	update_margin_indicator(frm) {
		const m = flt(frm.doc.gross_margin_percent);
		const color = m >= 25 ? "green" : m >= 15 ? "orange" : "red";
		const label = m >= 25 ? "Good Margin" : m >= 15 ? "Low Margin" : "Below Minimum";
		frm.page.set_indicator(`${__(label)}: ${flt(m, 1)}%`, color);
	},
});

frappe.ui.form.on("Catering Cost Sheet Item", {
	qty(frm, cdt, cdn) { _calc_cs_item(cdt, cdn); },
	rate(frm, cdt, cdn) { _calc_cs_item(cdt, cdn); },
});
function _calc_cs_item(cdt, cdn) {
	const r = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "amount", flt(r.qty) * flt(r.rate));
}
