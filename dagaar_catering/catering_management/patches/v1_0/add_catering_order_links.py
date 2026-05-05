# Copyright (c) 2024, DagaarSoft and contributors
# License: MIT
"""
Patch — Add `catering_order` Custom Field to ERPNext docs.

Runs after migrate, by which time the Catering Order DocType is fully synced.
Idempotent — safe to run multiple times.
"""
import frappe


def execute():
	"""Add `catering_order` Link field to ERPNext sales/buying/manufacturing docs."""
	# Prerequisite: Catering Order DocType must be installed
	if not frappe.db.exists("DocType", "Catering Order"):
		print("Patch: Catering Order DocType not yet installed — skipping.")
		return

	targets = [
		"Quotation", "Sales Order", "Sales Invoice", "Payment Entry",
		"Journal Entry", "Delivery Note", "Material Request",
		"Purchase Order", "Purchase Invoice", "Work Order", "Stock Entry",
	]

	added = 0
	for dt in targets:
		if not frappe.db.exists("DocType", dt):
			continue
		cf_name = f"{dt}-catering_order"
		if frappe.db.exists("Custom Field", cf_name):
			continue
		try:
			cf = frappe.new_doc("Custom Field")
			cf.dt = dt
			cf.label = "Catering Order"
			cf.fieldname = "catering_order"
			cf.fieldtype = "Link"
			cf.options = "Catering Order"
			cf.insert_after = "naming_series"
			cf.in_standard_filter = 1
			cf.print_hide = 1
			cf.description = "Reference to the Catering Order this document was created from"
			cf.insert(ignore_permissions=True)
			added += 1
		except Exception as e:
			# Silently skip any individual failure — continue with the rest
			pass

	frappe.db.commit()
	print(f"Patch: added catering_order Custom Field to {added} doctypes.")
