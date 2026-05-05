frappe.query_reports["Catering PL by Order"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date",
		   default: frappe.datetime.add_months(frappe.datetime.nowdate(), -3) },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date",
		   default: frappe.datetime.nowdate() },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "status", label: __("Status"), fieldtype: "Select", options: "\nConfirmed\nIn Production\nDelivered\nClosed" },
	],
};
