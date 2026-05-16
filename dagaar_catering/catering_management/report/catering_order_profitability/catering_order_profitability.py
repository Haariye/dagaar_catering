# Copyright (c) 2026, DagaarSoft
# License: MIT
"""Catering Order P&L — live profitability report.

Pulls numbers from dagaar_catering.catering_management.controllers.catering_order
.get_profitability() — same source the form cards use, so the report and the
form always agree.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	columns = _get_columns()
	data = _get_data(filters)
	summary = _get_summary(data)
	return columns, data, None, None, summary


def _get_columns():
	return [
		{"label": _("Catering Order"), "fieldname": "name", "fieldtype": "Link",
		 "options": "Catering Order", "width": 140},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
		{"label": _("Event Date"), "fieldname": "event_date", "fieldtype": "Date", "width": 100},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
		{"label": _("Revenue"), "fieldname": "revenue", "fieldtype": "Currency",
		 "options": "currency", "width": 110},
		{"label": _("Food"), "fieldname": "food_cost", "fieldtype": "Currency",
		 "options": "currency", "width": 100},
		{"label": _("Beverage"), "fieldname": "beverage_cost", "fieldtype": "Currency",
		 "options": "currency", "width": 100},
		{"label": _("Snacks"), "fieldname": "snacks_cost", "fieldtype": "Currency",
		 "options": "currency", "width": 100},
		{"label": _("Labor"), "fieldname": "labor_cost", "fieldtype": "Currency",
		 "options": "currency", "width": 100},
		{"label": _("Delivery"), "fieldname": "delivery_cost", "fieldtype": "Currency",
		 "options": "currency", "width": 100},
		{"label": _("Rental"), "fieldname": "rental_cost", "fieldtype": "Currency",
		 "options": "currency", "width": 100},
		{"label": _("Overhead"), "fieldname": "overhead_cost", "fieldtype": "Currency",
		 "options": "currency", "width": 100},
		{"label": _("Total Cost"), "fieldname": "total_cost", "fieldtype": "Currency",
		 "options": "currency", "width": 120},
		{"label": _("Gross Profit"), "fieldname": "gross_profit", "fieldtype": "Currency",
		 "options": "currency", "width": 120},
		{"label": _("Margin %"), "fieldname": "gross_margin_percent",
		 "fieldtype": "Percent", "width": 90},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data",
		 "width": 70, "hidden": 1},
	]


def _get_data(filters):
	from dagaar_catering.catering_management.controllers.catering_order import \
		_compute_catering_pnl

	where = ["docstatus != 2"]
	args = {}
	if filters.get("catering_order"):
		where.append("name = %(catering_order)s")
		args["catering_order"] = filters["catering_order"]
	if filters.get("customer"):
		where.append("customer = %(customer)s")
		args["customer"] = filters["customer"]
	if filters.get("from_date"):
		where.append("event_date >= %(from_date)s")
		args["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		where.append("event_date <= %(to_date)s")
		args["to_date"] = filters["to_date"]
	if filters.get("status"):
		where.append("status = %(status)s")
		args["status"] = filters["status"]

	orders = frappe.db.sql(f"""
		SELECT name, customer_name, event_date, status, currency
		FROM `tabCatering Order`
		WHERE {' AND '.join(where)}
		ORDER BY event_date DESC, name DESC
		LIMIT 500
	""", args, as_dict=True)

	rows = []
	for o in orders:
		pnl = _compute_catering_pnl(o.name) or {}
		rows.append({
			"name": o.name,
			"customer_name": o.customer_name,
			"event_date": o.event_date,
			"status": o.status,
			"currency": pnl.get("currency", o.currency),
			"revenue":       pnl.get("revenue", 0),
			"food_cost":     pnl.get("food_cost", 0),
			"beverage_cost": pnl.get("beverage_cost", 0),
			"snacks_cost":   pnl.get("snacks_cost", 0),
			"labor_cost":    pnl.get("labor_cost", 0),
			"delivery_cost": pnl.get("delivery_cost", 0),
			"rental_cost":   pnl.get("rental_cost", 0),
			"overhead_cost": pnl.get("overhead_cost", 0),
			"total_cost":    pnl.get("total_cost", 0),
			"gross_profit":  pnl.get("gross_profit", 0),
			"gross_margin_percent": pnl.get("gross_margin_percent", 0),
		})
	return rows


def _get_summary(rows):
	rev = sum(flt(r["revenue"]) for r in rows)
	cost = sum(flt(r["total_cost"]) for r in rows)
	profit = rev - cost
	margin = (profit / rev * 100) if rev else 0
	return [
		{"value": rev, "label": _("Total Revenue"), "datatype": "Currency",
		 "indicator": "Blue"},
		{"value": cost, "label": _("Total Cost"), "datatype": "Currency",
		 "indicator": "Orange"},
		{"value": profit, "label": _("Gross Profit"), "datatype": "Currency",
		 "indicator": "Green" if profit >= 0 else "Red"},
		{"value": round(margin, 2), "label": _("Avg Margin %"), "datatype": "Percent",
		 "indicator": "Green" if margin >= 20 else "Orange" if margin >= 10 else "Red"},
	]
