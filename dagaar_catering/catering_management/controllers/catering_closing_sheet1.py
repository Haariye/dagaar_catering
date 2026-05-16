# Copyright (c) 2024, DagaarSoft — catering_closing_sheet.py

import frappe
from frappe import _
from frappe.utils import flt


def validate(doc, method=None):
	_calculate_pl(doc)


def on_submit(doc, method=None):
	frappe.db.set_value("Catering Order", doc.catering_order, {
		"closing_sheet": doc.name,
		"status": "Closed"
	})


def _calculate_pl(doc):
	doc.gross_profit = flt(doc.total_revenue) - flt(doc.total_cost)
	doc.net_profit = flt(doc.gross_profit)
	if flt(doc.total_revenue):
		doc.gross_margin_percent = flt(doc.gross_profit) / flt(doc.total_revenue) * 100
	else:
		doc.gross_margin_percent = 0

	planned = frappe.db.sql(
		"SELECT IFNULL(gross_margin_percent,0) FROM `tabCatering Cost Sheet` WHERE catering_order=%s AND docstatus=1 LIMIT 1",
		doc.catering_order
	)
	doc.planned_margin_percent = flt(planned[0][0]) if planned else 0
	doc.margin_variance = flt(doc.gross_margin_percent) - flt(doc.planned_margin_percent)
