// dagaar_catering.js — Global helpers
window.DagaarCatering = {
	format_currency(amount, currency) {
		return frappe.format(amount, { fieldtype: "Currency", currency });
	},
	margin_color(pct) {
		if (pct >= 25) return "green";
		if (pct >= 15) return "orange";
		return "red";
	},
};
