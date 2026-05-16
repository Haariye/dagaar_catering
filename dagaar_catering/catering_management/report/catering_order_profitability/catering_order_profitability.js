// Copyright (c) 2026, DagaarSoft
frappe.query_reports["Catering Order Profitability"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 0 },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 0 },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "catering_order", label: __("Catering Order"), fieldtype: "Link",
		  options: "Catering Order" },
		{ fieldname: "status", label: __("Status"), fieldtype: "Select",
		  options: ["", "Draft", "Confirmed", "Invoiced", "Paid", "In Production",
		            "Delivered", "Closed", "Cancelled"].join("\n") },
	],
	formatter: function(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "gross_margin_percent" && data) {
			const m = parseFloat(data.gross_margin_percent);
			let color = "#7f8c8d";
			if (m >= 25) color = "#27ae60";
			else if (m >= 15) color = "#f39c12";
			else if (m < 0) color = "#c0392b";
			return `<span style="font-weight:600;color:${color};">${value}</span>`;
		}
		if (column.fieldname === "gross_profit" && data) {
			const p = parseFloat(data.gross_profit);
			const color = p >= 0 ? "#27ae60" : "#c0392b";
			return `<span style="color:${color};">${value}</span>`;
		}
		return value;
	},
};
