# Copyright (c) 2024, DagaarSoft and contributors
# License: MIT
"""
Catering Cost Sheet — Advanced Natural Costing v2.2

Auto-populates costs from real ERPNext data on every save.
On submit, posts a Journal Entry to GL for accounting visibility.
All accounts pulled from Catering Settings.
"""

import frappe
from frappe import _
from frappe.utils import flt, today


def validate(doc, method=None):
	if not doc.catering_order:
		return
	_compute_revenue(doc)
	_compute_food_costs(doc)
	_compute_labor_cost(doc)
	_compute_delivery_and_rental_costs(doc)
	_compute_wastage_and_other(doc)
	_compute_totals_and_margin(doc)


def on_submit(doc, method=None):
	validate(doc)
	try:
		je_name = _post_cost_journal_entry(doc)
		if je_name:
			if hasattr(doc, 'journal_entry'):
				frappe.db.set_value("Catering Cost Sheet", doc.name, "journal_entry", je_name)
			frappe.msgprint(_("Posted Journal Entry: {0}").format(
				frappe.utils.get_link_to_form("Journal Entry", je_name)),
				indicator="green", alert=True)
	except Exception as e:
		frappe.log_error(f"Cost Sheet JE failed: {str(e)[:300]}", "Catering Cost Sheet")
		frappe.msgprint(_("Cost sheet saved, but JE posting failed: {0}").format(str(e)[:200]),
			indicator="orange")


def on_cancel(doc, method=None):
	if doc.get("journal_entry"):
		try:
			je = frappe.get_doc("Journal Entry", doc.journal_entry)
			if je.docstatus == 1:
				je.cancel()
		except Exception:
			pass


# ─── Cost computations ──────────────────────────────────────────────────────

def _compute_revenue(doc):
	co = frappe.db.get_value("Catering Order", doc.catering_order,
		["sales_invoice", "total_order_value", "currency"], as_dict=True) or {}
	if co.get("sales_invoice"):
		si_total = frappe.db.get_value("Sales Invoice", co.sales_invoice, "grand_total")
		doc.total_revenue = flt(si_total) if si_total else flt(co.get("total_order_value", 0))
	else:
		doc.total_revenue = flt(co.get("total_order_value", 0))


def _compute_food_costs(doc):
	doc.food_cost = _consumption_for_categories(doc.catering_order, ["Food", "Dessert"])
	doc.beverage_cost = _consumption_for_categories(doc.catering_order, ["Beverage"])
	doc.snacks_cost = _consumption_for_categories(doc.catering_order, ["Snacks"])
	if hasattr(doc, 'packaging_cost'):
		doc.packaging_cost = _consumption_for_categories(doc.catering_order, ["Packaging"])


def _compute_labor_cost(doc):
	doc.labor_cost = _safe_sum("tabWork Order", "operating_cost", doc.catering_order)


def _compute_delivery_and_rental_costs(doc):
	settings = _get_settings()
	delivery_acct = settings.get("default_delivery_cost_account") if settings else None
	doc.delivery_cost = _purchase_cost_by_account(doc.catering_order, delivery_acct) \
		if delivery_acct else _purchase_cost_by_keyword(doc.catering_order,
			["delivery", "transport", "logistics"])
	doc.rental_cost = _purchase_cost_by_keyword(doc.catering_order,
		["rental", "rent", "equipment", "hire"])


def _compute_wastage_and_other(doc):
	wastage = _safe_sum("tabCatering Wastage Entry", "total_wastage_value", doc.catering_order)
	emergency = _safe_sum("tabCatering Emergency Expense", "total_amount", doc.catering_order)
	doc.overhead_cost = flt(wastage) + flt(emergency)


def _compute_totals_and_margin(doc):
	cost_fields = ['food_cost', 'beverage_cost', 'snacks_cost', 'labor_cost',
				   'delivery_cost', 'rental_cost', 'overhead_cost', 'packaging_cost']
	doc.total_cost = sum(flt(getattr(doc, f, 0)) for f in cost_fields)
	doc.gross_profit = flt(doc.total_revenue) - flt(doc.total_cost)
	doc.gross_margin_percent = (
		doc.gross_profit / flt(doc.total_revenue) * 100
		if flt(doc.total_revenue) else 0
	)


# ─── Database queries ───────────────────────────────────────────────────────

def _consumption_for_categories(catering_order, categories):
	"""Sum stock consumption for items in given categories."""
	try:
		placeholders = ",".join(["%s"] * len(categories))
		items = frappe.db.sql(f"""
			SELECT DISTINCT coi.item_code
			FROM `tabCatering Order Item` coi
			WHERE coi.parent = %s
			  AND coi.category IN ({placeholders})
		""", tuple([catering_order] + list(categories)), as_dict=True)

		item_codes = [r.item_code for r in items if r.item_code]
		if not item_codes:
			return 0

		ph = ",".join(["%s"] * len(item_codes))
		result = frappe.db.sql(f"""
			SELECT IFNULL(SUM(ABS(sle.stock_value_difference)), 0)
			FROM `tabStock Ledger Entry` sle
			INNER JOIN `tabStock Entry` se ON se.name = sle.voucher_no
			WHERE sle.voucher_type = 'Stock Entry'
			  AND se.docstatus = 1
			  AND se.catering_order = %s
			  AND sle.actual_qty < 0
			  AND sle.item_code IN ({ph})
		""", tuple([catering_order] + item_codes))
		return flt(result[0][0]) if result else 0
	except Exception:
		return 0


def _safe_sum(table, column, catering_order, extra_where=""):
	try:
		col_check = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = %s
			  AND column_name = 'catering_order'
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


def _get_settings():
	try:
		return frappe.get_single("Catering Settings")
	except Exception:
		return None


# ─── Journal Entry posting ──────────────────────────────────────────────────

def _post_cost_journal_entry(doc):
	"""Post a JE that debits each cost category and credits inventory/accrual."""
	if not flt(doc.total_cost):
		return None

	settings = _get_settings()
	if not settings:
		raise Exception("Catering Settings not configured")

	co = frappe.get_doc("Catering Order", doc.catering_order)
	company = doc.company or co.company

	credit_account = settings.get("default_cogs_account") or \
					 frappe.db.get_value("Company", company, "default_expense_account")
	if not credit_account:
		raise Exception("No default credit account configured")

	cost_map = [
		("food_cost", settings.get("default_food_cogs_account") or settings.get("default_cogs_account"), "Food Cost"),
		("beverage_cost", settings.get("default_food_cogs_account") or settings.get("default_cogs_account"), "Beverage Cost"),
		("snacks_cost", settings.get("default_food_cogs_account") or settings.get("default_cogs_account"), "Snacks Cost"),
		("labor_cost", settings.get("default_labor_cost_account") or settings.get("default_expense_account"), "Labor Cost"),
		("delivery_cost", settings.get("default_delivery_cost_account") or settings.get("default_expense_account"), "Delivery Cost"),
		("rental_cost", settings.get("default_expense_account"), "Rental Cost"),
		("overhead_cost", settings.get("default_wastage_account") or settings.get("default_expense_account"), "Wastage / Overhead"),
		("packaging_cost", settings.get("default_expense_account"), "Packaging Cost"),
	]

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.posting_date = doc.cost_sheet_date or today()
	je.company = company
	je.user_remark = f"Cost Sheet {doc.name} for Catering Order {doc.catering_order}"
	je.naming_series = _pick_naming_series("Journal Entry")

	total_dr = 0
	for field, account, desc in cost_map:
		amount = flt(getattr(doc, field, 0))
		if amount <= 0 or not account:
			continue
		je.append("accounts", {
			"account": account,
			"debit_in_account_currency": amount,
			"cost_center": co.cost_center,
			"project": co.project,
			"user_remark": desc,
		})
		total_dr += amount

	if total_dr <= 0:
		return None

	je.append("accounts", {
		"account": credit_account,
		"credit_in_account_currency": total_dr,
		"cost_center": co.cost_center,
		"project": co.project,
		"user_remark": "Cost Sheet aggregate credit",
	})

	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	return je.name


def _pick_naming_series(doctype):
	try:
		meta = frappe.get_meta(doctype)
		field = meta.get_field("naming_series")
		if field and field.options:
			opts = [x.strip() for x in field.options.split("\n") if x.strip()]
			if opts:
				return opts[0]
	except Exception:
		pass
	return "ACC-JV-.YYYY.-" if doctype == "Journal Entry" else ""
