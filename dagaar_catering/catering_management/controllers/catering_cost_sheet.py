# Copyright (c) 2024, DagaarSoft and contributors
# License: MIT

import frappe
from frappe import _
from frappe.utils import flt


def validate(doc, method=None):
	_calculate_totals(doc)
	_fetch_order_data(doc)
	_check_minimum_margin(doc)


def on_submit(doc, method=None):
	frappe.db.set_value("Catering Order", doc.catering_order, "cost_sheet", doc.name)


def _calculate_totals(doc):
	# Sum items table
	item_total = sum(flt(i.amount) for i in (doc.items or []))

	# Use item total only if greater than manually entered costs
	if item_total > 0:
		doc.food_cost = doc.food_cost or 0
		doc.beverage_cost = doc.beverage_cost or 0
		doc.snacks_cost = doc.snacks_cost or 0
		doc.packaging_cost = doc.packaging_cost or 0
		doc.labor_cost = doc.labor_cost or 0
		doc.delivery_cost = doc.delivery_cost or 0
		doc.rental_cost = doc.rental_cost or 0
		doc.overhead_cost = doc.overhead_cost or 0

		# Recalculate item amounts
		for item in (doc.items or []):
			item.amount = flt(item.qty) * flt(item.rate)

	doc.total_cost = (flt(doc.food_cost) + flt(doc.beverage_cost) + flt(doc.snacks_cost)
					  + flt(doc.packaging_cost) + flt(doc.labor_cost) + flt(doc.delivery_cost)
					  + flt(doc.rental_cost) + flt(doc.overhead_cost))

	if flt(doc.total_revenue) > 0:
		doc.gross_profit = flt(doc.total_revenue) - doc.total_cost
		doc.gross_margin_percent = (doc.gross_profit / flt(doc.total_revenue)) * 100
	else:
		doc.gross_profit = 0
		doc.gross_margin_percent = 0


def _fetch_order_data(doc):
	if doc.catering_order:
		order = frappe.db.get_value(
			"Catering Order",
			doc.catering_order,
			["customer_name", "event_date", "total_guests", "grand_total", "company"],
			as_dict=True
		)
		if order:
			doc.customer = order.customer_name
			doc.event_date = order.event_date
			doc.total_guests = order.total_guests
			doc.total_revenue = order.grand_total
			if not doc.company:
				doc.company = order.company


def _check_minimum_margin(doc):
	try:
		min_margin = flt(frappe.db.get_single_value("Catering Settings", "minimum_margin_percent") or 15)
	except Exception:
		min_margin = 15.0

	if flt(doc.gross_margin_percent) < min_margin and flt(doc.total_cost) > 0:
		frappe.msgprint(
			_("Warning: Gross Margin {0:.1f}% is below the minimum {1:.1f}%.").format(
				doc.gross_margin_percent, min_margin),
			alert=True,
			indicator="orange"
		)
