# Copyright (c) 2024, DagaarSoft and contributors
# License: MIT
"""
Linkers — keep Catering Order in sync with linked ERPNext documents.

Triggered via doc_events on Sales Order, Sales Invoice, Payment Entry, Delivery Note.
Updates total_paid, status, and link fields on the parent Catering Order.
"""
import frappe
from frappe.utils import flt


def update_quotation_status(doc, method=None):
	"""Quotation submit → mark Catering Order Quoted (legacy support)."""
	if not doc.get("catering_order"):
		return
	try:
		if doc.docstatus == 1:
			frappe.db.set_value("Catering Order", doc.catering_order,
				{"quotation": doc.name, "status": "Quoted"}, update_modified=False)
		elif doc.docstatus == 2:
			frappe.db.set_value("Catering Order", doc.catering_order, "quotation", None,
				update_modified=False)
	except Exception:
		pass


def update_so_status(doc, method=None):
	"""Sales Order submit/cancel → update parent Catering Order."""
	if not doc.get("catering_order"):
		return
	try:
		if doc.docstatus == 1:
			frappe.db.set_value("Catering Order", doc.catering_order,
				{"sales_order": doc.name, "status": "Confirmed"}, update_modified=False)
		elif doc.docstatus == 2:
			frappe.db.set_value("Catering Order", doc.catering_order, "sales_order", None,
				update_modified=False)
	except Exception:
		pass


def update_si_status(doc, method=None):
	"""Sales Invoice submit/cancel → update Catering Order, recompute totals."""
	if not doc.get("catering_order"):
		return
	try:
		if doc.docstatus == 1:
			# Recompute total_paid (might already have payments referencing this invoice)
			total_paid = _recalculate_total_paid(doc.catering_order, sales_invoice=doc.name)
			outstanding = flt(doc.outstanding_amount)
			status = "Paid" if outstanding <= 0 else "Invoiced"
			# Compute & store the snapshot so future order changes can be detected
			try:
				from dagaar_catering.catering_management.controllers.catering_order import _compute_order_snapshot
				co = frappe.get_doc("Catering Order", doc.catering_order)
				snapshot = _compute_order_snapshot(co)
			except Exception:
				snapshot = None
			update_data = {
				"sales_invoice": doc.name,
				"total_paid": total_paid,
				"deposit_received": total_paid,  # alias for backward compat
				"balance_due": flt(doc.grand_total) - total_paid,
				"status": status,
				"requires_rebill": 0,  # fresh billing — no rebill needed
			}
			if snapshot:
				update_data["last_billed_snapshot"] = snapshot
			frappe.db.set_value("Catering Order", doc.catering_order, update_data, update_modified=False)
		elif doc.docstatus == 2:
			frappe.db.set_value("Catering Order", doc.catering_order, "sales_invoice", None,
				update_modified=False)
	except Exception as e:
		frappe.log_error(f"linker.update_si_status error: {str(e)[:200]}", "Catering Linker")


def update_payment_status(doc, method=None):
	"""Payment Entry submit/cancel → recompute total_paid on Catering Order.

	Triggers on:
	1. Direct payment (catering_order field set on PE)
	2. Indirect payment (PE references the Sales Invoice linked to a Catering Order)
	"""
	# Find affected Catering Orders
	catering_orders = set()

	# Direct via catering_order field
	if doc.get("catering_order"):
		catering_orders.add(doc.catering_order)

	# Indirect via Sales Invoice references
	for ref in (doc.get("references") or []):
		if ref.reference_doctype == "Sales Invoice":
			co = frappe.db.get_value("Catering Order",
				{"sales_invoice": ref.reference_name}, "name")
			if co:
				catering_orders.add(co)

	# Update each affected Catering Order
	for co_name in catering_orders:
		try:
			total_paid = _recalculate_total_paid(co_name)
			co_data = frappe.db.get_value("Catering Order", co_name,
				["total_order_value", "sales_invoice"], as_dict=True) or {}

			update_data = {
				"total_paid": total_paid,
				"deposit_received": total_paid,
				"balance_due": flt(co_data.get("total_order_value", 0)) - total_paid,
			}

			# Update status if invoice is fully paid
			if co_data.get("sales_invoice"):
				outstanding = flt(frappe.db.get_value("Sales Invoice",
					co_data["sales_invoice"], "outstanding_amount"))
				if outstanding <= 0:
					update_data["status"] = "Paid"

			frappe.db.set_value("Catering Order", co_name, update_data, update_modified=False)
		except Exception as e:
			frappe.log_error(f"linker.update_payment_status error for {co_name}: {str(e)[:200]}",
				"Catering Linker")


def update_dn_status(doc, method=None):
	"""Delivery Note submit → update Catering Order to Delivered."""
	if not doc.get("catering_order"):
		return
	try:
		if doc.docstatus == 1:
			frappe.db.set_value("Catering Order", doc.catering_order,
				{"delivery_note": doc.name, "status": "Delivered"}, update_modified=False)
	except Exception:
		pass


def update_wo_status(doc, method=None):
	"""Work Order events → log only (status driven by Production Plan)."""
	pass


# ─── Internal helpers ────────────────────────────────────────────────────────

def _recalculate_total_paid(catering_order, sales_invoice=None):
	"""Compute total received from all submitted Payment Entries for a Catering Order."""
	total = 0.0

	# Direct payments via catering_order field
	try:
		col_check = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = 'tabPayment Entry'
			  AND column_name = 'catering_order'
		""")
		if col_check and col_check[0][0] > 0:
			direct = frappe.db.sql("""
				SELECT IFNULL(SUM(paid_amount), 0) FROM `tabPayment Entry`
				WHERE docstatus = 1 AND payment_type = 'Receive' AND catering_order = %s
			""", catering_order)
			total += flt(direct[0][0]) if direct else 0
	except Exception:
		pass

	# Indirect via Sales Invoice references (avoid double-counting)
	if not sales_invoice:
		sales_invoice = frappe.db.get_value("Catering Order", catering_order, "sales_invoice")

	if sales_invoice:
		try:
			via_invoice = frappe.db.sql("""
				SELECT IFNULL(SUM(per.allocated_amount), 0)
				FROM `tabPayment Entry` pe
				INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
				WHERE pe.docstatus = 1
				  AND pe.payment_type = 'Receive'
				  AND per.reference_doctype = 'Sales Invoice'
				  AND per.reference_name = %s
				  AND (pe.catering_order IS NULL OR pe.catering_order != %s)
			""", (sales_invoice, catering_order))
			total += flt(via_invoice[0][0]) if via_invoice else 0
		except Exception:
			pass

	return total
