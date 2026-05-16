# Copyright (c) 2024, DagaarSoft — catering_closing_sheet.py
"""
Catering Closing Sheet — Final P&L + Order Close

On validate: generates a Cost Sheet if missing, pulls latest figures.
On submit: blocks if outstanding > 0 (unless bypass), posts summary JE, closes order.
"""

import frappe
from frappe import _
from frappe.utils import flt, today


def validate(doc, method=None):
	if not doc.catering_order:
		frappe.throw(_("Catering Order is required."))

	# Ensure a Cost Sheet exists for this order
	cs_name = frappe.db.get_value("Catering Cost Sheet",
		{"catering_order": doc.catering_order, "docstatus": ["!=", 2]}, "name")
	if not cs_name:
		cs_name = _create_cost_sheet(doc.catering_order)

	# Pull latest figures from Cost Sheet
	if cs_name:
		cs = frappe.get_doc("Catering Cost Sheet", cs_name)
		# Trigger recompute on Cost Sheet so it has latest data
		try:
			cs.save(ignore_permissions=True)
		except Exception:
			pass
		doc.total_revenue = flt(cs.total_revenue)
		doc.total_cost = flt(cs.total_cost)
		doc.food_cost = flt(cs.food_cost)
		doc.beverage_cost = flt(cs.beverage_cost) if hasattr(cs, "beverage_cost") else 0
		doc.labor_cost = flt(cs.labor_cost)
		doc.delivery_cost = flt(cs.delivery_cost)

	_calculate_pl(doc)
	_check_outstanding(doc)


def on_submit(doc, method=None):
	"""Close the order and post the summary Journal Entry."""
	# Close the Catering Order
	frappe.db.set_value("Catering Order", doc.catering_order, {
		"closing_sheet": doc.name,
		"status": "Closed",
	}, update_modified=False)

	# Close the linked Project
	project = frappe.db.get_value("Catering Order", doc.catering_order, "project")
	if project:
		try:
			frappe.db.set_value("Project", project, "status", "Completed", update_modified=False)
		except Exception:
			pass

	# Post summary JE
	try:
		je_name = _post_summary_journal_entry(doc)
		if je_name and hasattr(doc, 'journal_entry'):
			frappe.db.set_value("Catering Closing Sheet", doc.name, "journal_entry", je_name)
			frappe.msgprint(_("Posted summary Journal Entry: {0}").format(
				frappe.utils.get_link_to_form("Journal Entry", je_name)),
				indicator="green", alert=True)
	except Exception as e:
		frappe.log_error(f"Closing Sheet JE failed: {str(e)[:300]}", "Catering Closing Sheet")
		frappe.msgprint(_("Closing Sheet submitted, but JE posting failed: {0}").format(
			str(e)[:200]), indicator="orange")


def on_cancel(doc, method=None):
	if doc.get("journal_entry"):
		try:
			je = frappe.get_doc("Journal Entry", doc.journal_entry)
			if je.docstatus == 1:
				je.flags.ignore_permissions = True
				je.cancel()
		except Exception:
			pass

	# Reopen the project and order
	project = frappe.db.get_value("Catering Order", doc.catering_order, "project")
	if project:
		try:
			frappe.db.set_value("Project", project, "status", "Open", update_modified=False)
		except Exception:
			pass
	frappe.db.set_value("Catering Order", doc.catering_order, "status", "Delivered",
		update_modified=False)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _calculate_pl(doc):
	"""Pull all P&L figures from the live source so Closing Sheet matches the cards."""
	try:
		from dagaar_catering.catering_management.controllers.catering_order import _compute_catering_pnl
		pnl = _compute_catering_pnl(doc.catering_order) or {}
	except Exception:
		pnl = {}

	doc.total_revenue = flt(pnl.get("revenue", 0))
	doc.total_cost = flt(pnl.get("total_cost", 0))
	doc.food_cost = flt(pnl.get("food_cost", 0))
	if hasattr(doc, "beverage_cost"):
		doc.beverage_cost = flt(pnl.get("beverage_cost", 0))
	doc.labor_cost = flt(pnl.get("labor_cost", 0))
	doc.delivery_cost = flt(pnl.get("delivery_cost", 0))

	doc.gross_profit = flt(doc.total_revenue) - flt(doc.total_cost)
	if flt(doc.total_revenue):
		doc.gross_margin_percent = flt(doc.gross_profit) / flt(doc.total_revenue) * 100
	else:
		doc.gross_margin_percent = 0
	if hasattr(doc, "net_profit"):
		doc.net_profit = flt(doc.gross_profit)


def _check_outstanding(doc):
	"""Block close if ANY Sales Invoice for this order has outstanding > 0.

	This covers the main SI plus any supplementary, credit notes, and
	Additional Service invoices. Manager can bypass via bypass_deposit
	checkbox on the Closing Sheet.
	"""
	outstanding_rows = frappe.db.sql("""
		SELECT name, outstanding_amount, grand_total, is_return
		FROM `tabSales Invoice`
		WHERE catering_order = %s
		  AND docstatus = 1
		  AND outstanding_amount > 0
	""", doc.catering_order, as_dict=True)

	if not outstanding_rows:
		return  # all paid

	if doc.get("bypass_outstanding") or doc.get("bypass_deposit"):
		# Manager has explicitly bypassed
		return

	is_mgr = any(r in frappe.get_roles() for r in
		("Catering Manager", "Catering Management",
		 "System Manager", "Administrator"))

	total_out = sum(flt(r.outstanding_amount) for r in outstanding_rows)
	currency = frappe.db.get_value("Catering Order", doc.catering_order, "currency")

	lines = [f"  • {r.name}: {r.outstanding_amount} {currency}"
	         for r in outstanding_rows]

	msg = _("Cannot close: {0} Sales Invoice(s) have unpaid balance totaling {1} {2}:").format(
		len(outstanding_rows), total_out, currency)
	msg += "<br>" + "<br>".join(lines) + "<br><br>"

	if is_mgr:
		msg += _("As a Manager, you can tick the <b>Bypass Outstanding Balance</b> "
		         "checkbox to override.")
	else:
		msg += _("Collect the outstanding payments or ask a Catering Manager to bypass.")

	frappe.throw(msg, title=_("Outstanding Balance"))



def _create_cost_sheet(catering_order):
	"""Helper: create a Cost Sheet for the order on the fly."""
	try:
		co = frappe.get_doc("Catering Order", catering_order)
		cs = frappe.new_doc("Catering Cost Sheet")
		cs.catering_order = catering_order
		cs.company = co.company
		cs.currency = co.currency
		cs.cost_sheet_date = today()
		cs.flags.ignore_permissions = True
		cs.insert(ignore_permissions=True)
		frappe.db.set_value("Catering Order", catering_order, "cost_sheet", cs.name,
			update_modified=False)
		return cs.name
	except Exception as e:
		frappe.log_error(f"_create_cost_sheet (closing) failed: {str(e)[:200]}",
			"Closing Sheet")
		return None


def _post_summary_journal_entry(doc):
	"""Post the per-event summary JE: debit each cost category, credit aggregate offset.

	This is the SINGLE accounting voucher for the entire catering event's costs,
	posted at close time. It's tagged with the project so Project Profitability works.
	"""
	if not flt(doc.total_cost):
		return None

	try:
		settings = frappe.get_single("Catering Settings")
	except Exception:
		return None

	co = frappe.get_doc("Catering Order", doc.catering_order)
	company = doc.company or co.company

	credit_account = settings.get("default_cogs_account") or \
		frappe.db.get_value("Company", company, "default_expense_account")
	if not credit_account:
		raise Exception("No credit account configured (set Default COGS Account in Catering Settings)")

	food_acct = settings.get("default_food_cogs_account") or settings.get("default_cogs_account")
	labor_acct = settings.get("default_labor_cost_account") or settings.get("default_expense_account")
	delivery_acct = settings.get("default_delivery_cost_account") or settings.get("default_expense_account")
	wastage_acct = settings.get("default_wastage_account") or settings.get("default_expense_account")
	generic_exp = settings.get("default_expense_account")

	cost_map = [
		("food_cost",     food_acct,     "Food Cost"),
		("beverage_cost", food_acct,     "Beverage Cost"),
		("labor_cost",    labor_acct,    "Labor Cost"),
		("delivery_cost", delivery_acct, "Delivery Cost"),
	]

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.posting_date = doc.closing_date or today()
	je.company = company
	je.user_remark = f"Closing Sheet {doc.name} for Catering Order {doc.catering_order}"
	je.naming_series = _pick_naming_series("Journal Entry")
	je.project = co.project

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
		"account": credit_account,
		"credit_in_account_currency": total_dr,
		"cost_center": co.cost_center,
		"project": co.project,
		"user_remark": "Closing aggregate credit",
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
