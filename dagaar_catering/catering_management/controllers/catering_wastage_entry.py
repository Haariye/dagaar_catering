# Copyright (c) 2024, DagaarSoft
"""Catering Wastage Entry — validation and totals."""

import frappe
from frappe import _
from frappe.utils import flt


def validate(doc, method=None):
	# Default warehouse from settings if blank
	if not doc.warehouse:
		try:
			settings = frappe.get_single("Catering Settings")
			doc.warehouse = (settings.get("default_wastage_warehouse")
			                 or settings.get("default_warehouse"))
		except Exception:
			pass

	# Compute amount per child row (qty × valuation_rate)
	total = 0
	for row in (doc.get("items") or []):
		if not row.valuation_rate and row.item_code:
			try:
				row.valuation_rate = flt(
					frappe.db.get_value("Item", row.item_code, "valuation_rate")
				)
			except Exception:
				pass
		# Cascade warehouse from parent if blank
		if not row.warehouse:
			row.warehouse = doc.warehouse
		row.amount = flt(row.qty) * flt(row.valuation_rate)
		total += flt(row.amount)

	doc.total_wastage_value = total


def on_submit(doc, method=None):
	"""Hook on submit — currently no extra action needed.

	The live profitability function reads Wastage values directly via SQL,
	so no cache refresh is required. This stub exists so the hook in hooks.py
	can resolve cleanly.
	"""
	pass


def on_cancel(doc, method=None):
	"""Hook on cancel — no-op for the same reason."""
	pass
