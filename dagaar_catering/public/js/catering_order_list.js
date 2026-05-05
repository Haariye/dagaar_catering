// catering_order_list.js
frappe.listview_settings["Catering Order"] = {
	add_fields: ["status", "event_date", "customer", "total_order_value"],
	get_indicator(doc) {
		const map = {
			"Draft":               ["Draft",             "gray"],
			"Pending Approval":    ["Pending Approval",  "orange"],
			"Approved":            ["Approved",          "blue"],
			"Confirmed":           ["Confirmed",         "blue"],
			"Sales Order Created": ["SO Created",        "purple"],
			"Deposit Received":    ["Deposit Received",  "purple"],
			"In Production":       ["In Production",     "orange"],
			"Ready for Delivery":  ["Ready",             "cyan"],
			"Delivered":           ["Delivered",         "green"],
			"Invoiced":            ["Invoiced",          "blue"],
			"Closed":              ["Closed",            "green"],
			"Cancelled":           ["Cancelled",         "red"],
		};
		return map[doc.status] || [doc.status, "gray"];
	},
};
