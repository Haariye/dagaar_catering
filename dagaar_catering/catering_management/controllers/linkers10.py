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
	"""Sales Order submit/cancel → update parent Catering Order + auto-create Project."""
	if not doc.get("catering_order"):
		return
	try:
		if doc.docstatus == 1:
			co = frappe.get_doc("Catering Order", doc.catering_order)

			# Auto-create Project if missing
			project_name = co.project
			if not project_name:
				project_name = _auto_create_project(co, doc)

			# Stamp the project on the Sales Order
			if project_name and not doc.project:
				frappe.db.set_value("Sales Order", doc.name, "project", project_name,
					update_modified=False)

			# Update the Catering Order
			update_data = {
				"sales_order": doc.name,
				"status": "Confirmed",
			}
			if project_name:
				update_data["project"] = project_name
			frappe.db.set_value("Catering Order", doc.catering_order, update_data, update_modified=False)

		elif doc.docstatus == 2:
			frappe.db.set_value("Catering Order", doc.catering_order, "sales_order", None,
				update_modified=False)
	except Exception as e:
		frappe.log_error(f"update_so_status error: {str(e)[:300]}", "Catering Linker")


def _auto_create_project(co, so):
	"""Create an ERPNext Project tied to this Catering Order.

	Project name: PROJ-{catering_order_name}
	The project captures all linked tasks, expenses, and time entries so the standard
	ERPNext Project Profitability report works out of the box.
	"""
	import hashlib
	project_name = f"PROJ-{co.name}"
	# Truncate to 140 chars max (Frappe limit)
	if len(project_name) > 140:
		project_name = project_name[:140]

	# If a project with this exact name already exists, just return it
	if frappe.db.exists("Project", project_name):
		return project_name

	try:
		proj = frappe.new_doc("Project")
		proj.project_name = project_name
		proj.naming_series = _project_naming_series()
		proj.status = "Open"
		proj.customer = co.customer
		proj.company = co.company or so.company
		proj.cost_center = co.cost_center
		proj.expected_start_date = co.event_date or frappe.utils.today()
		proj.expected_end_date = co.event_end_date or co.event_date or frappe.utils.today()
		proj.is_active = "Yes"
		proj.project_type = "External"
		proj.estimated_costing = flt(co.total_order_value) * 0.7  # rough estimate
		proj.notes = f"Catering Order: {co.name} — Customer: {co.customer_name or co.customer}"

		proj.flags.ignore_permissions = True
		proj.insert(ignore_permissions=True)
		return proj.name
	except Exception as e:
		frappe.log_error(f"_auto_create_project failed: {str(e)[:300]}", "Catering Project Creation")
		return None


def _project_naming_series():
	try:
		meta = frappe.get_meta("Project")
		field = meta.get_field("naming_series")
		if field and field.options:
			opts = [x.strip() for x in field.options.split("\n") if x.strip()]
			if opts:
				return opts[0]
	except Exception:
		pass
	return "PROJ-.####"




def update_si_status(doc, method=None):
	"""Sales Invoice submit/cancel → update Catering Order, recompute totals."""
	# SKIP additional service invoices — they have their own life and
	# must NOT touch co.sales_invoice (which is reserved for the package SI).
	if getattr(doc, "is_additional_service", 0):
		return

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


def _auto_create_cost_sheet(catering_order):
	"""Create the Cost Sheet for live cost tracking AND auto-submit it.

	After submit, the Cost Sheet still absorbs new expenses because every
	cost field has allow_on_submit=1, and various event hooks call
	refresh_cost_sheet(...) on every relevant submit/cancel.
	"""
	try:
		co = frappe.get_doc("Catering Order", catering_order)
		cs = frappe.new_doc("Catering Cost Sheet")
		cs.catering_order = catering_order
		cs.company = co.company
		cs.currency = co.currency
		cs.cost_sheet_date = frappe.utils.today()
		cs.flags.ignore_permissions = True
		cs.insert(ignore_permissions=True)
		try:
			cs.submit()
		except Exception as e:
			frappe.log_error(f"Cost Sheet auto-submit failed: {str(e)[:200]}",
				"Catering Cost Sheet")
		frappe.db.set_value("Catering Order", catering_order, "cost_sheet", cs.name,
			update_modified=False)
		return cs.name
	except Exception as e:
		frappe.log_error(f"_auto_create_cost_sheet failed for {catering_order}: {str(e)[:300]}",
			"Catering Auto Cost Sheet")
		return None


def refresh_cost_sheet_on_event(doc, method=None):
	"""DEPRECATED — kept as a no-op for hook backward compat.

	Cost Sheet is no longer cached/stored. P&L is computed live by
	catering_order.get_profitability() on every read. No event-driven
	refresh is needed.
	"""
	pass


def propagate_catering_order_to_stock_entry(doc, method=None):
	"""Stock Entry validate/before_save hook.

	Populates `doc.catering_order` (custom field) from the linked Work Order
	if not already set. Without this, Cost Sheet can't find the consumption
	values because Stock Entries created from Work Orders don't carry the
	custom field automatically.
	"""
	# Already tagged? Nothing to do
	if doc.get("catering_order"):
		return

	# Try via the work_order link
	wo = doc.get("work_order")
	if wo:
		try:
			co = frappe.db.get_value("Work Order", wo, "catering_order")
			if co:
				doc.catering_order = co
				return
		except Exception:
			pass

	# Try via Material Request reference (if present in items)
	try:
		for row in (doc.get("items") or []):
			mr = getattr(row, "material_request", None)
			if mr:
				co = frappe.db.get_value("Material Request", mr, "catering_order")
				if co:
					doc.catering_order = co
					return
	except Exception:
		pass

	# Try via project field (ERPNext's standard linker)
	proj = doc.get("project")
	if proj:
		try:
			co = frappe.db.get_value("Catering Order", {"project": proj}, "name")
			if co:
				doc.catering_order = co
				return
		except Exception:
			pass


def set_stock_entry_expense_account(doc, method=None):
	"""Override expense_account on each item row when Stock Entry is linked
	to a catering_order. Routes consumption through Stock Adjustment to
	avoid COGS double-count (COGS posts only from Delivery Note).
	"""
	if not getattr(doc, "catering_order", None):
		return
	try:
		settings = frappe.get_single("Catering Settings")
		sa_account = settings.get("default_stock_adjustment_account")
		if not sa_account:
			return  # not configured — leave default ERPNext behavior
		for row in (doc.get("items") or []):
			row.expense_account = sa_account
	except Exception:
		pass
