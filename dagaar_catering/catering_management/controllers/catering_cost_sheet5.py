# Copyright (c) 2024, DagaarSoft — catering_cost_sheet.py
"""
Catering Cost Sheet — Live Costing (non-submittable)

Auto-recomputes on every save. Always editable. No JE posted here — the summary
Journal Entry is posted by the Closing Sheet at event close.
"""

import frappe
from frappe import _
from frappe.utils import flt


def validate(doc, method=None):
	if not doc.catering_order:
		return
	_compute_revenue(doc)
	_compute_costs(doc)
	_compute_totals(doc)


# ─── Revenue ────────────────────────────────────────────────────────────────

def _compute_revenue(doc):
	co = frappe.db.get_value("Catering Order", doc.catering_order,
		["sales_invoice", "total_order_value", "currency"], as_dict=True) or {}
	if co.get("sales_invoice"):
		# Sum all Sales Invoices for this order (including supplementary)
		result = frappe.db.sql("""
			SELECT IFNULL(SUM(
				CASE WHEN is_return = 1 THEN -ABS(grand_total) ELSE grand_total END
			), 0)
			FROM `tabSales Invoice`
			WHERE catering_order = %s AND docstatus = 1
		""", doc.catering_order)
		doc.total_revenue = flt(result[0][0]) if result else flt(co.get("total_order_value", 0))
	else:
		doc.total_revenue = flt(co.get("total_order_value", 0))


# ─── Costs ──────────────────────────────────────────────────────────────────

def _compute_costs(doc):
	"""Compute all cost categories from natural sources."""

	# Food categories - from Stock Ledger via Stock Entries
	doc.food_cost = _consumption_for_categories(doc.catering_order, ["Food", "Dessert"])
	doc.beverage_cost = _consumption_for_categories(doc.catering_order, ["Beverage"])
	doc.snacks_cost = _consumption_for_categories(doc.catering_order, ["Snacks"])
	if hasattr(doc, 'packaging_cost'):
		doc.packaging_cost = _consumption_for_categories(doc.catering_order, ["Packaging"])

	# Labor cost from Work Orders
	doc.labor_cost = _safe_sum("tabWork Order", "operating_cost", doc.catering_order)

	# Delivery & Rental from Purchase Invoices
	settings = _settings()
	delivery_acct = settings.get("default_delivery_cost_account") if settings else None
	doc.delivery_cost = _purchase_cost_by_account(doc.catering_order, delivery_acct) if delivery_acct \
		else _purchase_cost_by_keyword(doc.catering_order, ["delivery", "transport", "logistics"])

	doc.rental_cost = _purchase_cost_by_keyword(doc.catering_order,
		["rental", "rent", "equipment", "hire"])

	# Wastage + Emergency Expense -> overhead
	wastage = _safe_sum("tabCatering Wastage Entry", "total_wastage_value", doc.catering_order)
	emergency = _safe_sum("tabCatering Emergency Expense", "total_amount", doc.catering_order)

	# ALSO include Quick Expenses (Journal Entries tagged with this catering_order)
	quick_je = _journal_entry_expense(doc.catering_order)

	doc.overhead_cost = flt(wastage) + flt(emergency) + flt(quick_je)


def _compute_totals(doc):
	cost_fields = ['food_cost', 'beverage_cost', 'snacks_cost', 'labor_cost',
	               'delivery_cost', 'rental_cost', 'overhead_cost', 'packaging_cost']
	doc.total_cost = sum(flt(getattr(doc, f, 0)) for f in cost_fields)
	doc.gross_profit = flt(doc.total_revenue) - flt(doc.total_cost)
	doc.gross_margin_percent = (
		doc.gross_profit / flt(doc.total_revenue) * 100
		if flt(doc.total_revenue) else 0
	)


# ─── Database queries ──────────────────────────────────────────────────────

def _consumption_for_categories(catering_order, categories):
	try:
		ph = ",".join(["%s"] * len(categories))
		items = frappe.db.sql(f"""
			SELECT DISTINCT coi.item_code
			FROM `tabCatering Order Item` coi
			WHERE coi.parent = %s AND coi.category IN ({ph})
		""", tuple([catering_order] + list(categories)), as_dict=True)
		item_codes = [r.item_code for r in items if r.item_code]
		if not item_codes:
			return 0
		ipp = ",".join(["%s"] * len(item_codes))
		result = frappe.db.sql(f"""
			SELECT IFNULL(SUM(ABS(sle.stock_value_difference)), 0)
			FROM `tabStock Ledger Entry` sle
			INNER JOIN `tabStock Entry` se ON se.name = sle.voucher_no
			WHERE sle.voucher_type = 'Stock Entry'
			  AND se.docstatus = 1
			  AND se.catering_order = %s
			  AND sle.actual_qty < 0
			  AND sle.item_code IN ({ipp})
		""", tuple([catering_order] + item_codes))
		return flt(result[0][0]) if result else 0
	except Exception:
		return 0


def _safe_sum(table, column, catering_order, extra_where=""):
	try:
		col_check = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = %s AND column_name = 'catering_order'
		""", table)
		if not col_check or col_check[0][0] == 0:
			return 0
		result = frappe.db.sql(f"""
			SELECT IFNULL(SUM({column}), 0) FROM `{table}`
			WHERE docstatus = 1 AND catering_order = %s {extra_where}
		""", catering_order)
		return flt(result[0][0]) if result else 0
	except Exception:
		return 0


def _purchase_cost_by_account(catering_order, account):
	if not account:
		return 0
	try:
		result = frappe.db.sql("""
			SELECT IFNULL(SUM(pii.amount), 0)
			FROM `tabPurchase Invoice` pi
			INNER JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
			WHERE pi.docstatus = 1
			  AND pi.catering_order = %s
			  AND pii.expense_account = %s
		""", (catering_order, account))
		return flt(result[0][0]) if result else 0
	except Exception:
		return 0


def _purchase_cost_by_keyword(catering_order, keywords):
	try:
		conditions = " OR ".join([
			"(LOWER(pii.item_name) LIKE %s OR LOWER(IFNULL(pii.description,'')) LIKE %s)"
			for _ in keywords
		])
		args = [catering_order]
		for kw in keywords:
			args.extend([f"%{kw.lower()}%", f"%{kw.lower()}%"])
		result = frappe.db.sql(f"""
			SELECT IFNULL(SUM(pii.amount), 0)
			FROM `tabPurchase Invoice` pi
			INNER JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
			WHERE pi.docstatus = 1
			  AND pi.catering_order = %s
			  AND ({conditions})
		""", tuple(args))
		return flt(result[0][0]) if result else 0
	except Exception:
		return 0


def _journal_entry_expense(catering_order):
	"""Sum debits to expense accounts from JEs tagged with this catering_order.
	Includes Quick Expenses."""
	try:
		col_check = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = 'tabJournal Entry'
			  AND column_name = 'catering_order'
		""")
		if not col_check or col_check[0][0] == 0:
			return 0
		result = frappe.db.sql("""
			SELECT IFNULL(SUM(jea.debit_in_account_currency), 0)
			FROM `tabJournal Entry` je
			INNER JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
			INNER JOIN `tabAccount` acc ON acc.name = jea.account
			WHERE je.docstatus = 1
			  AND je.catering_order = %s
			  AND acc.root_type = 'Expense'
		""", catering_order)
		return flt(result[0][0]) if result else 0
	except Exception:
		return 0


def _settings():
	try:
		return frappe.get_single("Catering Settings")
	except Exception:
		return None
