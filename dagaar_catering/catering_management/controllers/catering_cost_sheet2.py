# Copyright (c) 2024, DagaarSoft and contributors
# License: MIT
"""Catering Cost Sheet — Auto-populates costs from ERPNext on every save."""

import frappe
from frappe import _
from frappe.utils import flt


def validate(doc, method=None):
	"""Auto-populate all cost fields from ERPNext on save."""
	if not doc.catering_order:
		return

	# Aggregate from Stock Ledger Entries (material consumption via Stock Entries)
	doc.food_cost = _get_consumption_cost_by_category(doc.catering_order, ["Food", "Dessert"])
	doc.beverage_cost = _get_consumption_cost_by_category(doc.catering_order, ["Beverage"])
	doc.snacks_cost = _get_consumption_cost_by_category(doc.catering_order, ["Snacks", "Packaging"])

	# Labor cost from Work Order operations
	doc.labor_cost = _get_labor_cost_from_work_orders(doc.catering_order)

	# Delivery / Rental from Purchase Invoice items
	doc.delivery_cost = _get_purchase_cost_by_keyword(doc.catering_order,
		["delivery", "transport", "logistics"])
	doc.rental_cost = _get_purchase_cost_by_keyword(doc.catering_order,
		["rental", "rent", "equipment"])

	# Wastage cost from Catering Wastage Entry
	doc.overhead_cost = _get_total_from_table(doc.catering_order,
		"tabCatering Wastage Entry", "total_wastage_value")

	# Compute total
	doc.total_cost = (
		flt(doc.food_cost) + flt(doc.beverage_cost) + flt(doc.snacks_cost) +
		flt(doc.labor_cost) + flt(doc.delivery_cost) + flt(doc.rental_cost) +
		flt(doc.overhead_cost) + flt(getattr(doc, 'packaging_cost', 0) or 0)
	)

	# Compute profit
	if doc.total_revenue and not flt(doc.total_revenue):
		doc.total_revenue = flt(frappe.db.get_value(
			"Catering Order", doc.catering_order, "total_order_value")) or 0

	doc.gross_profit = flt(doc.total_revenue) - flt(doc.total_cost)
	doc.gross_margin_percent = (
		doc.gross_profit / flt(doc.total_revenue) * 100
		if flt(doc.total_revenue) else 0
	)


def on_submit(doc, method=None):
	"""Recompute on submit too."""
	validate(doc)


def _get_consumption_cost_by_category(catering_order, categories):
	"""Sum consumption cost from Stock Ledger Entries linked via Stock Entries
	with catering_order = X, filtered by item categories from Catering Order Items."""
	try:
		# Get item codes by category from this Catering Order
		items = frappe.db.sql("""
			SELECT item_code FROM `tabCatering Order Item`
			WHERE parent = %s AND category IN %s
		""", (catering_order, tuple(categories) if len(categories) > 1 else (categories[0],)),
			as_dict=True)
		item_codes = [i.item_code for i in items if i.item_code]
		if not item_codes:
			return 0

		# Sum stock value out from Stock Ledger Entries
		# voucher_no must be a Stock Entry that has catering_order = X
		result = frappe.db.sql("""
			SELECT IFNULL(SUM(ABS(sle.stock_value_difference)), 0)
			FROM `tabStock Ledger Entry` sle
			INNER JOIN `tabStock Entry` se ON se.name = sle.voucher_no
			WHERE sle.voucher_type = 'Stock Entry'
			  AND se.docstatus = 1
			  AND se.catering_order = %s
			  AND sle.actual_qty < 0
			  AND sle.item_code IN %s
		""", (catering_order, tuple(item_codes) if len(item_codes) > 1 else (item_codes[0],)))
		return flt(result[0][0]) if result else 0
	except Exception:
		return 0


def _get_labor_cost_from_work_orders(catering_order):
	"""Sum operating cost from Work Orders linked to this catering order."""
	try:
		result = frappe.db.sql("""
			SELECT IFNULL(SUM(operating_cost), 0)
			FROM `tabWork Order`
			WHERE docstatus = 1 AND catering_order = %s
		""", catering_order)
		return flt(result[0][0]) if result else 0
	except Exception:
		return 0


def _get_purchase_cost_by_keyword(catering_order, keywords):
	"""Sum Purchase Invoice items whose item_name/description contains the keywords."""
	try:
		# Build OR condition for keywords
		conditions = " OR ".join([
			f"(LOWER(pii.item_name) LIKE '%{kw.lower()}%' OR LOWER(pii.description) LIKE '%{kw.lower()}%')"
			for kw in keywords
		])
		result = frappe.db.sql(f"""
			SELECT IFNULL(SUM(pii.amount), 0)
			FROM `tabPurchase Invoice` pi
			INNER JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
			WHERE pi.docstatus = 1
			  AND pi.catering_order = %s
			  AND ({conditions})
		""", catering_order)
		return flt(result[0][0]) if result else 0
	except Exception:
		return 0


def _get_total_from_table(catering_order, table, column):
	"""Generic SUM helper that respects column existence."""
	try:
		col_check = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = %s AND column_name = 'catering_order'
		""", table)
		if not col_check or col_check[0][0] == 0:
			return 0
		result = frappe.db.sql(f"""
			SELECT IFNULL(SUM({column}), 0) FROM `{table}`
			WHERE docstatus = 1 AND catering_order = %s
		""", catering_order)
		return flt(result[0][0]) if result else 0
	except Exception:
		return 0
