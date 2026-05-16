# Copyright (c) 2024, DagaarSoft — catering_cost_sheet.py
"""
Catering Cost Sheet — Natural Costing Methodology v2.3

==========================================================================
COST CATEGORIES & SOURCES (all auto, no manual entry)
==========================================================================

  1. FOOD INGREDIENT COST
     Source: Stock Ledger Entries from Stock Entries tagged with this catering_order
     Filter: items whose Catering Order Item category = Food, Dessert, Beverage, Snacks
     Formula: SUM(ABS(stock_value_difference)) where actual_qty < 0
     → Uses ACTUAL moving-average valuation rate at consumption time.

  2. PACKAGING & DISPOSABLES COST
     Source: Same as above, filtered by category = Packaging
     → Captures plates, cups, napkins, takeaway containers, etc.

  3. LABOR COST
     Source: Work Order operating_cost (from BOM operations × time × hourly rate)
     Filter: Work Orders tagged with this catering_order
     Formula: SUM(operating_cost) where docstatus = 1
     → Captures actual cooking, prep, and service labor.

  4. DELIVERY & LOGISTICS COST
     Source: Purchase Invoice items tagged with this catering_order
     Filter: items posted to Settings.default_delivery_cost_account (preferred)
             OR description matches keywords: delivery, transport, logistics
     → Vehicle fuel, courier fees, third-party transport invoices.

  5. RENTAL & EQUIPMENT COST
     Source: Purchase Invoice items tagged with this catering_order
     Filter: description matches: rental, rent, equipment, hire
     → Crockery rentals, tent rentals, AV equipment, etc.

  6. WASTAGE COST
     Source: Catering Wastage Entry — total_wastage_value
     Filter: docstatus = 1, catering_order = X
     → Spoilage and disposal costs.

  7. EMERGENCY / ADDITIONAL EXPENSE
     Source: Catering Emergency Expense — total_amount
     Filter: docstatus = 1, catering_order = X
     → Approved last-minute purchases.

==========================================================================
DERIVED METRICS
==========================================================================

  Total Direct Cost     = Food + Packaging + Labor
  Total Logistics Cost  = Delivery + Rental
  Total Overhead Cost   = Wastage + Emergency
  TOTAL COST            = sum of all above
  TOTAL REVENUE         = Sales Invoice grand_total (post-discount)
                          OR Catering Order total_order_value (fallback)
  GROSS PROFIT          = TOTAL REVENUE − TOTAL COST
  GROSS MARGIN %        = GROSS PROFIT / TOTAL REVENUE × 100

==========================================================================
JOURNAL ENTRY ON SUBMIT
==========================================================================
  Dr Food COGS         (Food + Packaging)
  Dr Labor Cost
  Dr Delivery Cost     (Delivery + Rental)
  Dr Wastage Expense   (Wastage + Emergency)
  Cr Cost of Goods Sold Clearing  (aggregate offset)
==========================================================================
"""

import frappe
from frappe import _
from frappe.utils import flt, today


def validate(doc, method=None):
	if not doc.catering_order:
		return
	_compute_revenue(doc)
	_compute_costs(doc)
	_compute_totals(doc)


def on_submit(doc, method=None):
	validate(doc)
	try:
		je_name = _post_je(doc)
		if je_name and hasattr(doc, 'journal_entry'):
			frappe.db.set_value("Catering Cost Sheet", doc.name, "journal_entry", je_name)
			frappe.msgprint(_("Posted Journal Entry: {0}").format(
				frappe.utils.get_link_to_form("Journal Entry", je_name)),
				indicator="green", alert=True)
	except Exception as e:
		frappe.log_error(f"Cost Sheet JE failed: {str(e)[:300]}", "Cost Sheet")
		frappe.msgprint(_("Cost sheet saved, but JE posting failed: {0}").format(str(e)[:200]),
			indicator="orange")


def on_cancel(doc, method=None):
	if doc.get("journal_entry"):
		try:
			je = frappe.get_doc("Journal Entry", doc.journal_entry)
			if je.docstatus == 1:
				je.flags.ignore_permissions = True
				je.cancel()
		except Exception:
			pass


# ─── Revenue ────────────────────────────────────────────────────────────────

def _compute_revenue(doc):
	co = frappe.db.get_value("Catering Order", doc.catering_order,
		["sales_invoice", "total_order_value", "currency"], as_dict=True) or {}
	if co.get("sales_invoice"):
		# Sum ALL Sales Invoices linked to this order (primary + supplementary + credit notes net)
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
	"""Compute all 7 cost categories from natural sources."""

	# 1. Food Ingredient Cost (Food + Dessert + Beverage + Snacks consumption)
	doc.food_cost = _consumption_for_categories(doc.catering_order, ["Food", "Dessert"])
	doc.beverage_cost = _consumption_for_categories(doc.catering_order, ["Beverage"])
	doc.snacks_cost = _consumption_for_categories(doc.catering_order, ["Snacks"])

	# 2. Packaging
	if hasattr(doc, 'packaging_cost'):
		doc.packaging_cost = _consumption_for_categories(doc.catering_order, ["Packaging"])

	# 3. Labor
	doc.labor_cost = _safe_sum("tabWork Order", "operating_cost", doc.catering_order)

	# 4. Delivery & Logistics
	settings = _settings()
	delivery_acct = settings.get("default_delivery_cost_account") if settings else None
	doc.delivery_cost = _purchase_cost_by_account(doc.catering_order, delivery_acct) if delivery_acct \
		else _purchase_cost_by_keyword(doc.catering_order, ["delivery", "transport", "logistics"])

	# 5. Rental
	doc.rental_cost = _purchase_cost_by_keyword(doc.catering_order,
		["rental", "rent", "equipment", "hire"])

	# 6 + 7. Wastage + Emergency → consolidated into overhead_cost
	wastage = _safe_sum("tabCatering Wastage Entry", "total_wastage_value", doc.catering_order)
	emergency = _safe_sum("tabCatering Emergency Expense", "total_amount", doc.catering_order)
	doc.overhead_cost = flt(wastage) + flt(emergency)


def _compute_totals(doc):
	cost_fields = ['food_cost', 'beverage_cost', 'snacks_cost', 'labor_cost',
	               'delivery_cost', 'rental_cost', 'overhead_cost', 'packaging_cost']
	doc.total_cost = sum(flt(getattr(doc, f, 0)) for f in cost_fields)
	doc.gross_profit = flt(doc.total_revenue) - flt(doc.total_cost)
	doc.gross_margin_percent = (
		doc.gross_profit / flt(doc.total_revenue) * 100
		if flt(doc.total_revenue) else 0
	)


# ─── Database helpers ──────────────────────────────────────────────────────

def _consumption_for_categories(catering_order, categories):
	"""Stock consumption value for items whose Catering Order Item category matches."""
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


def _settings():
	try:
		return frappe.get_single("Catering Settings")
	except Exception:
		return None


# ─── JE posting ─────────────────────────────────────────────────────────────

def _post_je(doc):
	if not flt(doc.total_cost):
		return None
	settings = _settings()
	if not settings:
		raise Exception("Catering Settings not configured")
	co = frappe.get_doc("Catering Order", doc.catering_order)
	company = doc.company or co.company

	credit = settings.get("default_cogs_account") or \
	         frappe.db.get_value("Company", company, "default_expense_account")
	if not credit:
		raise Exception("No credit account (set Default COGS Account in Catering Settings)")

	# Use settings accounts with sensible fallbacks
	food_acct = settings.get("default_food_cogs_account") or settings.get("default_cogs_account")
	labor_acct = settings.get("default_labor_cost_account") or settings.get("default_expense_account")
	delivery_acct = settings.get("default_delivery_cost_account") or settings.get("default_expense_account")
	wastage_acct = settings.get("default_wastage_account") or settings.get("default_expense_account")
	generic_exp = settings.get("default_expense_account")

	cost_map = [
		("food_cost",      food_acct,     "Food Cost"),
		("beverage_cost",  food_acct,     "Beverage Cost"),
		("snacks_cost",    food_acct,     "Snacks Cost"),
		("packaging_cost", food_acct,     "Packaging Cost"),
		("labor_cost",     labor_acct,    "Labor Cost"),
		("delivery_cost",  delivery_acct, "Delivery Cost"),
		("rental_cost",    generic_exp,   "Rental Cost"),
		("overhead_cost",  wastage_acct,  "Wastage & Overhead"),
	]

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.posting_date = doc.cost_sheet_date or today()
	je.company = company
	je.user_remark = f"Cost Sheet {doc.name} for Catering Order {doc.catering_order}"
	je.naming_series = _ns("Journal Entry")

	total_dr = 0
	for field, acct, desc in cost_map:
		amt = flt(getattr(doc, field, 0))
		if amt <= 0 or not acct:
			continue
		je.append("accounts", {
			"account": acct,
			"debit_in_account_currency": amt,
			"cost_center": co.cost_center,
			"project": co.project,
			"user_remark": desc,
		})
		total_dr += amt

	if total_dr <= 0:
		return None

	je.append("accounts", {
		"account": credit,
		"credit_in_account_currency": total_dr,
		"cost_center": co.cost_center,
		"project": co.project,
		"user_remark": "Cost Sheet aggregate credit",
	})

	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	return je.name


def _ns(doctype):
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
