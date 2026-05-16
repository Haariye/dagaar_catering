# Copyright (c) 2024, DagaarSoft — catering_cost_sheet.py
"""
Catering Cost Sheet — Self-Updating Submittable Cost Tracker

After auto-submit (which happens once the first cost data arrives), the sheet
keeps absorbing expenses because every cost field carries allow_on_submit=1.
Linkers trigger a save on this Cost Sheet whenever upstream events happen
(Wastage submitted, Stock Entry submitted, Quick Expense JE posted, Sales
Invoice updated, etc.) and the validate() hook recomputes everything from
scratch using natural sources.
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


def on_submit(doc, method=None):
	"""No JE posted here — that happens at Closing Sheet."""
	pass


def on_cancel(doc, method=None):
	pass


# ─── Revenue ────────────────────────────────────────────────────────────────

def _compute_revenue(doc):
	co = frappe.db.get_value("Catering Order", doc.catering_order,
		["sales_invoice", "total_order_value", "currency"], as_dict=True) or {}
	if co.get("sales_invoice"):
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
	"""Pull all cost categories from natural sources.

	Step 1: Set all category buckets to 0.
	Step 2: Add Stock Entry consumption (Manufacture/Material Issue/Material Transfer
	        for Manufacture) — bucketed by item category.
	Step 3: Add Work Order operating_cost → labor_cost.
	Step 4: Add Wastage → overhead_cost.
	Step 5: Add Emergency Expense → overhead_cost.
	Step 6: Add Quick Expense JEs → bucket by debit-account match with Settings.
	Step 7: Add Purchase Invoice (Delivery/Rental keyword) → delivery/rental.
	"""
	# Reset all buckets
	doc.food_cost = 0
	doc.beverage_cost = 0
	doc.snacks_cost = 0
	if hasattr(doc, "packaging_cost"):
		doc.packaging_cost = 0
	doc.labor_cost = 0
	doc.delivery_cost = 0
	doc.rental_cost = 0
	doc.overhead_cost = 0

	co_name = doc.catering_order
	settings = _settings()

	# ── Step 2: Stock Entry consumption (Manufacture, Material Issue, etc.) ──
	# Group by item category from Catering Order Item
	doc.food_cost     += _stock_consumption_for_categories(co_name, ["Food", "Dessert"])
	doc.beverage_cost += _stock_consumption_for_categories(co_name, ["Beverage"])
	doc.snacks_cost   += _stock_consumption_for_categories(co_name, ["Snacks"])
	if hasattr(doc, "packaging_cost"):
		doc.packaging_cost += _stock_consumption_for_categories(co_name, ["Packaging"])

	# ── Step 3: Work Order operating cost ──
	doc.labor_cost += _safe_sum("tabWork Order", "operating_cost", co_name)

	# ── Step 4: Wastage ──
	doc.overhead_cost += _safe_sum("tabCatering Wastage Entry", "total_wastage_value", co_name)

	# ── Step 5: Emergency Expense ──
	doc.overhead_cost += _safe_sum("tabCatering Emergency Expense", "total_amount", co_name)

	# ── Step 6: Quick Expense JEs → bucket by debit account ──
	je_buckets = _quick_expense_jes_by_account(co_name, settings)
	doc.food_cost     += je_buckets.get("food", 0)
	doc.labor_cost    += je_buckets.get("labor", 0)
	doc.delivery_cost += je_buckets.get("delivery", 0)
	doc.overhead_cost += je_buckets.get("overhead", 0)

	# ── Step 7: Purchase Invoice items (rental keyword) ──
	doc.rental_cost += _purchase_cost_by_keyword(co_name,
		["rental", "rent", "equipment", "hire"])

	# Delivery from Purchase Invoices on the Delivery account
	if settings:
		delivery_acct = settings.get("default_delivery_cost_account")
		if delivery_acct:
			doc.delivery_cost += _purchase_cost_by_account(co_name, delivery_acct)


def _compute_totals(doc):
	cost_fields = ['food_cost', 'beverage_cost', 'snacks_cost', 'labor_cost',
	               'delivery_cost', 'rental_cost', 'overhead_cost']
	if hasattr(doc, "packaging_cost"):
		cost_fields.append("packaging_cost")
	doc.total_cost = sum(flt(getattr(doc, f, 0)) for f in cost_fields)
	doc.gross_profit = flt(doc.total_revenue) - flt(doc.total_cost)
	doc.gross_margin_percent = (
		doc.gross_profit / flt(doc.total_revenue) * 100
		if flt(doc.total_revenue) else 0
	)


# ─── Helpers ──────────────────────────────────────────────────────────────

def _stock_consumption_for_categories(catering_order, categories):
	"""Return total consumption value for items whose CATEGORY (on the
	Catering Order Item) is in the given list.

	How consumption is mapped to category:
	  - For Stock Entries with purpose='Manufacture': consumed raw materials
	    are bucketed by the production_item's category (look up production_item
	    in Catering Order Item to find its category).
	  - For other purposes ('Material Issue' etc): consumed item's category is
	    looked up in Catering Order Item; if not found, falls back to 'Food'.

	Every negative SLE row (actual_qty < 0) from a Stock Entry tagged with
	the catering_order is counted exactly once and assigned to one bucket.
	"""
	try:
		# Build a map: item_code → category (from Catering Order Item)
		item_categories = {}
		rows = frappe.db.sql("""
			SELECT item_code, category
			FROM `tabCatering Order Item`
			WHERE parent = %s AND item_code IS NOT NULL
		""", catering_order, as_dict=True)
		for r in rows:
			if r.item_code and r.category:
				item_categories[r.item_code] = r.category

		if not item_categories:
			return 0

		# Get all Stock Entry consumption rows tagged with this catering_order
		sles = frappe.db.sql("""
			SELECT se.name AS se_name,
			       se.purpose AS purpose,
			       se.work_order AS work_order,
			       sle.item_code AS sle_item,
			       ABS(sle.stock_value_difference) AS amount
			FROM `tabStock Ledger Entry` sle
			INNER JOIN `tabStock Entry` se ON se.name = sle.voucher_no
			WHERE sle.voucher_type = 'Stock Entry'
			  AND se.docstatus = 1
			  AND se.catering_order = %s
			  AND se.purpose IN ('Manufacture', 'Material Issue', 'Material Transfer for Manufacture', 'Repack')
			  AND sle.actual_qty < 0
		""", catering_order, as_dict=True)

		# Cache production_item categories (per Work Order)
		wo_categories = {}
		total = 0
		for row in sles:
			category = None

			# Try: SLE item is itself a Catering Order Item → use its category
			if row.sle_item in item_categories:
				category = item_categories[row.sle_item]

			# Fall back: For Manufacture, look up the Work Order's production_item
			if not category and row.work_order:
				if row.work_order not in wo_categories:
					try:
						prod_item = frappe.db.get_value("Work Order",
							row.work_order, "production_item")
						wo_categories[row.work_order] = item_categories.get(prod_item)
					except Exception:
						wo_categories[row.work_order] = None
				category = wo_categories.get(row.work_order)

			# Fall back: default to "Food" for any uncategorized consumption
			if not category:
				category = "Food"

			if category in categories:
				total += flt(row.amount)

		return flt(total)
	except Exception:
		import traceback
		frappe.log_error(traceback.format_exc()[:500], "Cost Sheet Stock Consumption")
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


def _quick_expense_jes_by_account(catering_order, settings):
	"""Read all submitted JEs tagged with this catering_order and bucket their
	expense-side debits by which Settings account they hit.

	Returns a dict: {food, labor, delivery, overhead} -> Currency
	"""
	buckets = {"food": 0, "labor": 0, "delivery": 0, "overhead": 0}
	try:
		col_check = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = 'tabJournal Entry'
			  AND column_name = 'catering_order'
		""")
		if not col_check or col_check[0][0] == 0:
			return buckets

		rows = frappe.db.sql("""
			SELECT jea.account, IFNULL(SUM(jea.debit_in_account_currency), 0) AS amt
			FROM `tabJournal Entry` je
			INNER JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
			INNER JOIN `tabAccount` acc ON acc.name = jea.account
			WHERE je.docstatus = 1
			  AND je.catering_order = %s
			  AND acc.root_type = 'Expense'
			  AND jea.debit_in_account_currency > 0
			GROUP BY jea.account
		""", catering_order, as_dict=True)

		food_acct = settings.get("default_food_cogs_account") if settings else None
		labor_acct = settings.get("default_labor_cost_account") if settings else None
		delivery_acct = settings.get("default_delivery_cost_account") if settings else None

		for r in rows:
			amt = flt(r.amt)
			if food_acct and r.account == food_acct:
				buckets["food"] += amt
			elif labor_acct and r.account == labor_acct:
				buckets["labor"] += amt
			elif delivery_acct and r.account == delivery_acct:
				buckets["delivery"] += amt
			else:
				buckets["overhead"] += amt
	except Exception:
		pass
	return buckets


def _settings():
	try:
		return frappe.get_single("Catering Settings")
	except Exception:
		return None


# ─── Public helpers used by linkers & whitelisted callers ────────────────

def refresh_cost_sheet(catering_order):
	"""Force-refresh the Cost Sheet for this Catering Order.

	IMPORTANT: For SUBMITTED Cost Sheets we bypass the ORM and write directly
	via frappe.db.set_value. The ORM's update_after_submit filter sometimes
	drops field changes silently even with allow_on_submit=1, so we route
	around it.

	Called by:
	  - Wastage Entry on_submit / on_cancel
	  - Stock Entry on_submit / on_cancel
	  - Journal Entry on_submit / on_cancel
	  - Purchase Invoice on_submit / on_cancel
	  - Emergency Expense on_submit
	"""
	cs_name = frappe.db.get_value("Catering Cost Sheet",
		{"catering_order": catering_order, "docstatus": ["!=", 2]}, "name")
	if not cs_name:
		return None

	try:
		cs = frappe.get_doc("Catering Cost Sheet", cs_name)

		if cs.docstatus == 0:
			# Draft — normal save path works fine
			cs.flags.ignore_permissions = True
			cs.save(ignore_permissions=True)
			return cs.name

		# Submitted — recompute in memory, then write each cost field via db.set_value
		validate(cs)   # this populates all the doc fields in memory

		# Collect the computed values
		updates = {
			"food_cost":     flt(cs.food_cost),
			"beverage_cost": flt(cs.beverage_cost),
			"snacks_cost":   flt(cs.snacks_cost),
			"labor_cost":    flt(cs.labor_cost),
			"delivery_cost": flt(cs.delivery_cost),
			"rental_cost":   flt(cs.rental_cost),
			"overhead_cost": flt(cs.overhead_cost),
			"total_cost":    flt(cs.total_cost),
			"total_revenue": flt(cs.total_revenue),
			"gross_profit":  flt(cs.gross_profit),
			"gross_margin_percent": flt(cs.gross_margin_percent),
		}
		if hasattr(cs, "packaging_cost"):
			updates["packaging_cost"] = flt(cs.packaging_cost)

		# Write straight to DB — bypasses the submitted-doc filter
		frappe.db.set_value("Catering Cost Sheet", cs_name, updates,
			update_modified=False)
		frappe.db.commit()
		return cs_name
	except Exception:
		import traceback
		frappe.log_error(traceback.format_exc()[:500], "Cost Sheet Refresh")
		return None
