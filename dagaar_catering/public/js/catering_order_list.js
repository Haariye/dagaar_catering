// catering_order_list.js — List view with force-delete cascade
frappe.listview_settings["Catering Order"] = {
	add_fields: ["status", "event_date", "customer", "total_order_value", "docstatus"],

	get_indicator(doc) {
		const map = {
			"Draft":               ["Draft",            "gray"],
			"Confirmed":           ["Confirmed",        "blue"],
			"Sales Order Created": ["SO Created",       "purple"],
			"Deposit Received":    ["Deposit Received", "purple"],
			"In Production":       ["In Production",    "orange"],
			"Ready for Delivery":  ["Ready",            "cyan"],
			"Ready to Deliver":    ["Ready",            "cyan"],
			"Delivered":           ["Delivered",        "green"],
			"Invoiced":            ["Invoiced",         "blue"],
			"Paid":                ["Paid",             "darkgreen"],
			"Closed":              ["Closed",           "green"],
			"Cancelled":           ["Cancelled",        "red"],
		};
		return map[doc.status] || [doc.status, "gray"];
	},

	onload(listview) {
		// Add bulk Force-Delete action — manager-only
		const manager_roles = ['Catering Manager', 'Catering Management',
		                       'System Manager', 'Administrator'];
		const user_roles = frappe.user_roles || [];
		const is_manager = manager_roles.some(r => user_roles.includes(r));
		if (!is_manager) return;

		listview.page.add_actions_menu_item(__("🗑 Force Delete (cascade cancel linked docs)"), () => {
			const selected = listview.get_checked_items();
			if (!selected.length) {
				frappe.msgprint(__("Select at least one Catering Order to delete."));
				return;
			}
			const names = selected.map(d => d.name);
			const msg = __(
				"<p>This will <b>force-cancel and delete</b> {0} Catering Order(s) along with " +
				"<b>all linked documents</b>: Sales Orders, Sales Invoices, Payment Entries, " +
				"Production Plans, Work Orders, Stock Entries, Delivery Plans/Notes, Cost Sheets, " +
				"Closing Sheets, Wastage Entries, Emergency Expenses, and tied Journal Entries.</p>" +
				"<p><b>This cannot be undone.</b></p>" +
				"<p>Continue?</p>",
				[names.length]
			);
			frappe.confirm(msg, () => {
				frappe.call({
					method: "dagaar_catering.catering_management.controllers.catering_order.bulk_delete_catering_orders",
					args: { names: JSON.stringify(names) },
					freeze: true,
					freeze_message: __("Force-deleting orders and cascading cancellations..."),
					callback: (r) => {
						if (r.message) {
							const deleted = (r.message.deleted || []).length;
							const failed = r.message.failed || [];
							let html = `<p><b>Deleted:</b> ${deleted}</p>`;
							if (failed.length) {
								html += "<p><b>Failed:</b></p><ul>";
								failed.forEach(f => {
									html += `<li>${f.name}: ${f.error}</li>`;
								});
								html += "</ul>";
							}
							frappe.msgprint({
								title: __("Bulk Delete Result"),
								message: html,
								indicator: failed.length ? "orange" : "green"
							});
							listview.refresh();
						}
					},
				});
			});
		}, true);
	},
};
