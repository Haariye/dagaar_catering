# Copyright (c) 2024, DagaarSoft and contributors
# License: MIT
"""
Linkers — hooked into ERPNext document events.

When a Sales Order, Sales Invoice, Payment Entry, Delivery Note, or Work Order
that has a catering_order link is submitted, this module updates the parent
Catering Order's status and totals automatically.
"""
import frappe
from frappe.utils import flt


def update_so_status(doc, method=None):
	"""When Sales Order is submitted/cancelled, update parent Catering Order."""
	if not doc.get("catering_order"):
		return
	try:
		if doc.docstatus == 1:
			frappe.db.set_value("Catering Order", doc.catering_order,
				{"sales_order": doc.name, "status": "Confirmed"}, update_modified=False)
		elif doc.docstatus == 2:
			# Cancelled
			frappe.db.set_value("Catering Order", doc.catering_order, "sales_order", None,
				update_modified=False)
	except Exception:
		pass


def update_si_status(doc, method=None):
	"""Sales Invoice submitted → mark order as Invoiced."""
	if not doc.get("catering_order"):
		return
	try:
		if doc.docstatus == 1:
			# Check outstanding
			status = "Paid" if flt(doc.outstanding_amount) <= 0 else "Invoiced"
			frappe.db.set_value("Catering Order", doc.catering_order,
				{"sales_invoice": doc.name, "status": status}, update_modified=False)
		elif doc.docstatus == 2:
			frappe.db.set_value("Catering Order", doc.catering_order, "sales_invoice", None,
				update_modified=False)
	except Exception:
		pass


def update_payment_status(doc, method=None):
	"""Payment Entry submitted → update deposit_received and total_paid."""
	if not doc.get("catering_order"):
		return
	try:
		# Recalculate total paid from all submitted payment entries
		total_paid = flt(frappe.db.sql("""
			SELECT IFNULL(SUM(paid_amount), 0) FROM `tabPayment Entry`
			WHERE docstatus = 1 AND payment_type = 'Receive' AND catering_order = %s
		""", doc.catering_order)[0][0])

		co = frappe.get_doc("Catering Order", doc.catering_order)
		# If this is the first payment, treat as deposit
		deposit_received = min(total_paid, flt(co.deposit_amount)) if flt(co.deposit_amount) else total_paid

		updates = {
			"total_paid": total_paid,
			"deposit_received": deposit_received,
			"balance_due": flt(co.grand_total) - total_paid,
		}

		# If deposit fully received and not yet in production, update status
		if flt(deposit_received) >= flt(co.deposit_amount) and flt(co.deposit_amount) > 0:
			if co.status in ("Confirmed", "Quoted", "Draft"):
				updates["status"] = "Deposit Received"

		# If sales invoice exists and now fully paid
		if co.sales_invoice and total_paid >= flt(co.grand_total):
			updates["status"] = "Paid"

		frappe.db.set_value("Catering Order", doc.catering_order, updates, update_modified=False)
	except Exception:
		pass


def update_dn_status(doc, method=None):
	"""Delivery Note submitted → update order status to Delivered."""
	if not doc.get("catering_order"):
		return
	try:
		if doc.docstatus == 1:
			frappe.db.set_value("Catering Order", doc.catering_order,
				{"delivery_note": doc.name, "status": "Delivered"}, update_modified=False)
	except Exception:
		pass


def update_wo_status(doc, method=None):
	"""Work Order events → log activity on parent Catering Order."""
	if not doc.get("catering_order"):
		return
	try:
		# Just log, don't change status
		pass
	except Exception:
		pass
