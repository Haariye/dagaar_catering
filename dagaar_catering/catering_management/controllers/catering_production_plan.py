# Copyright (c) 2024, DagaarSoft — catering_production_plan.py

import frappe
from frappe import _
from frappe.utils import flt


def validate(doc, method=None):
	_fetch_order_data(doc)
	if not doc.items:
		frappe.throw(_("Production Plan must have at least one item."))


def on_submit(doc, method=None):
	frappe.db.set_value("Catering Order", doc.catering_order, {
		"production_plan": doc.name,
		"status": "In Production"
	})


def _fetch_order_data(doc):
	if doc.catering_order:
		order = frappe.db.get_value(
			"Catering Order", doc.catering_order,
			["customer_name", "event_date", "total_guests", "company"], as_dict=True
		)
		if order:
			doc.customer = order.customer_name
			doc.event_date = order.event_date
			doc.total_guests = order.total_guests
			if not doc.company:
				doc.company = order.company
