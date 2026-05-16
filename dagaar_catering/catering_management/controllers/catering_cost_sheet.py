# Copyright (c) 2024, DagaarSoft — catering_cost_sheet.py
"""
Catering Cost Sheet — reads from the SAME live source as the profitability cards.

The Cost Sheet doctype still exists for users who want to snapshot a cost
report. On validate(), it pulls live numbers from get_profitability() so
that the Cost Sheet, Profitability Cards, and Profitability Report all
agree.
"""

import frappe
from frappe import _
from frappe.utils import flt


def validate(doc, method=None):
	if not doc.catering_order:
		return

	# Pull live numbers via the canonical source
	try:
		from dagaar_catering.catering_management.controllers.catering_order import _compute_catering_pnl
		pnl = _compute_catering_pnl(doc.catering_order) or {}
	except Exception:
		pnl = {}

	doc.total_revenue   = flt(pnl.get("revenue", 0))
	doc.food_cost       = flt(pnl.get("food_cost", 0))
	doc.beverage_cost   = flt(pnl.get("beverage_cost", 0))
	doc.snacks_cost     = flt(pnl.get("snacks_cost", 0))
	if hasattr(doc, "packaging_cost"):
		doc.packaging_cost = flt(pnl.get("packaging_cost", 0))
	doc.labor_cost      = flt(pnl.get("labor_cost", 0))
	doc.delivery_cost   = flt(pnl.get("delivery_cost", 0))
	doc.rental_cost     = flt(pnl.get("rental_cost", 0))
	doc.overhead_cost   = flt(pnl.get("overhead_cost", 0))

	doc.total_cost      = flt(pnl.get("total_cost", 0))
	doc.gross_profit    = flt(pnl.get("gross_profit", 0))
	doc.gross_margin_percent = flt(pnl.get("gross_margin_percent", 0))


def refresh_cost_sheet(catering_order):
	"""Kept for backward compat — no-op now.

	The Cost Sheet pulls live values on every save via validate(),
	and nothing else needs to write to it.
	"""
	return None
