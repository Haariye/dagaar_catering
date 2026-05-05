# Copyright (c) 2024, DagaarSoft — catering_emergency_expense.py

import frappe
from frappe import _
from frappe.utils import flt


def validate(doc, method=None):
	doc.total_amount = sum(flt(i.amount) for i in (doc.items or []))
	for item in (doc.items or []):
		item.amount = flt(item.qty) * flt(item.rate)
	doc.total_amount = sum(flt(i.amount) for i in (doc.items or []))


def on_submit(doc, method=None):
	if doc.approval_status != "Approved":
		frappe.throw(_("Emergency Expense must be Approved before submitting."))
	_create_journal_entry(doc)


def _create_journal_entry(doc):
	if doc.journal_entry:
		return
	try:
		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.posting_date = doc.expense_date
		je.company = doc.company
		je.user_remark = f"Emergency Expense: {doc.name} | Order: {doc.catering_order}"

		expense_account = (doc.expense_account
						   or frappe.db.get_single_value("Catering Settings", "default_expense_account")
						   or frappe.db.get_value("Account", {"account_type": "Expense Account", "company": doc.company}, "name"))

		payable_account = frappe.db.get_value("Account", {"account_type": "Payable", "company": doc.company}, "name")

		if expense_account and payable_account:
			je.append("accounts", {"account": expense_account, "debit_in_account_currency": flt(doc.total_amount), "cost_center": None})
			je.append("accounts", {"account": payable_account, "credit_in_account_currency": flt(doc.total_amount), "party_type": "Supplier", "party": doc.supplier})
			je.insert(ignore_permissions=True)
			frappe.db.set_value("Catering Emergency Expense", doc.name, "journal_entry", je.name)
	except Exception as e:
		frappe.log_error(f"JE creation error for {doc.name}: {e}")


@frappe.whitelist()
def approve_emergency_expense(doc_name):
	doc = frappe.get_doc("Catering Emergency Expense", doc_name)
	doc.approval_status = "Approved"
	doc.approved_by = frappe.session.user
	doc.save(ignore_permissions=True)
	return "Approved"
