# Copyright (c) 2024, DagaarSoft — catering_wastage_entry.py
"""
Catering Wastage Entry — Records wastage and posts to GL automatically.

On submit:
1. Compute total wastage value from line items (qty × valuation_rate)
2. Create a Stock Entry (Material Issue) to deduct stock
3. Create a Journal Entry: Dr Wastage Expense, Cr Inventory (via stock entry GL)
"""

import frappe
from frappe import _
from frappe.utils import flt, today


def validate(doc, method=None):
	"""Apply settings defaults if blank."""
	_apply_settings_defaults(doc)
	_recompute_totals(doc)


def on_submit(doc, method=None):
	"""On submit: ensure totals, create Stock Entry, post JE."""
	_recompute_totals(doc)
	if doc.warehouse and not doc.get("stock_entry"):
		try:
			se_name = _create_stock_entry(doc)
			if se_name:
				frappe.db.set_value("Catering Wastage Entry", doc.name, "stock_entry", se_name)
		except Exception as e:
			frappe.log_error(f"Stock Entry for wastage {doc.name}: {e}", "Wastage Entry")

	try:
		je_name = _create_journal_entry(doc)
		if je_name and hasattr(doc, 'journal_entry'):
			frappe.db.set_value("Catering Wastage Entry", doc.name, "journal_entry", je_name)
	except Exception as e:
		frappe.log_error(f"JE for wastage {doc.name}: {e}", "Wastage Entry JE")


def on_cancel(doc, method=None):
	"""Cancel the linked Stock Entry and JE."""
	for linked_field, linked_doctype in [("stock_entry", "Stock Entry"),
										   ("journal_entry", "Journal Entry")]:
		linked = doc.get(linked_field)
		if linked:
			try:
				d = frappe.get_doc(linked_doctype, linked)
				if d.docstatus == 1:
					d.cancel()
			except Exception:
				pass


def _apply_settings_defaults(doc):
	"""Fill blank fields from Catering Settings."""
	if doc.warehouse and doc.company:
		return
	settings = _get_settings()
	if not settings:
		return
	if not doc.warehouse:
		doc.warehouse = settings.get("default_wastage_warehouse") or \
						settings.get("default_source_warehouse")
	if not doc.company:
		doc.company = settings.get("default_company")


def _recompute_totals(doc):
	"""Recompute item amounts and grand total."""
	for item in (doc.items or []):
		item.amount = flt(item.qty) * flt(item.valuation_rate)
	doc.total_wastage_value = sum(flt(i.amount) for i in (doc.items or []))


def _create_stock_entry(doc):
	"""Create the stock issue."""
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Issue"
	se.purpose = "Material Issue"
	se.posting_date = doc.posting_date or today()
	se.company = doc.company
	se.catering_order = doc.catering_order
	se.remarks = f"Wastage: {doc.name}"
	for item in (doc.items or []):
		se.append("items", {
			"item_code": item.item_code,
			"qty": flt(item.qty),
			"uom": item.uom,
			"s_warehouse": doc.warehouse,
			"basic_rate": flt(item.valuation_rate),
		})
	se.flags.ignore_permissions = True
	se.insert(ignore_permissions=True)
	se.submit()
	return se.name


def _create_journal_entry(doc):
	"""Post wastage to GL: Dr Wastage Expense, Cr a clearing/inventory account."""
	if not flt(doc.total_wastage_value):
		return None

	settings = _get_settings()
	if not settings:
		return None

	wastage_account = settings.get("default_wastage_account") or \
					  settings.get("default_expense_account")
	credit_account = settings.get("default_cogs_account") or \
					 frappe.db.get_value("Company", doc.company, "default_expense_account")

	if not wastage_account or not credit_account:
		return None

	co = frappe.db.get_value("Catering Order", doc.catering_order,
		["cost_center", "project"], as_dict=True) or {}

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.posting_date = doc.posting_date or today()
	je.company = doc.company
	je.user_remark = f"Wastage from Catering Order {doc.catering_order} — {doc.name}"
	je.naming_series = _pick_naming_series("Journal Entry")

	je.append("accounts", {
		"account": wastage_account,
		"debit_in_account_currency": flt(doc.total_wastage_value),
		"cost_center": co.get("cost_center"),
		"project": co.get("project"),
		"user_remark": "Wastage Expense",
	})
	je.append("accounts", {
		"account": credit_account,
		"credit_in_account_currency": flt(doc.total_wastage_value),
		"cost_center": co.get("cost_center"),
		"project": co.get("project"),
		"user_remark": "Inventory / accrual",
	})

	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	return je.name


def _get_settings():
	try:
		return frappe.get_single("Catering Settings")
	except Exception:
		return None


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
