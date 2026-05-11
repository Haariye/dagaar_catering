# Copyright (c) 2024, DagaarSoft and contributors
# License: MIT
"""
Patch — Add `catering_order` Custom Field to ERPNext docs.

This version:
- Prints exactly what happens for each doctype (so silent failures are visible)
- Falls back to raw SQL ALTER TABLE if Frappe Custom Field API fails
- Idempotent — safe to run multiple times
"""
import frappe


TARGETS = [
	"Quotation",
	"Sales Order",
	"Sales Invoice",
	"Payment Entry",
	"Journal Entry",
	"Delivery Note",
	"Material Request",
	"Purchase Order",
	"Purchase Invoice",
	"Work Order",
	"Stock Entry",
]


def execute():
	"""Add `catering_order` Link field to ERPNext docs that should reverse-link."""

	if not frappe.db.exists("DocType", "Catering Order"):
		print("ERROR: Catering Order DocType does not exist. Aborting patch.")
		return

	added = 0
	already = 0
	failed = 0
	column_added = 0

	for dt in TARGETS:
		if not frappe.db.exists("DocType", dt):
			print(f"  SKIP {dt}: DocType not installed")
			continue

		cf_name = f"{dt}-catering_order"

		# Step 1: Create the Custom Field if it doesn't exist
		if frappe.db.exists("Custom Field", cf_name):
			already += 1
			print(f"  [exists] {dt}: Custom Field already present")
		else:
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
				cf.flags.ignore_validate = True
				cf.flags.ignore_permissions = True
				cf.insert(ignore_permissions=True)
				added += 1
				print(f"  [added]  {dt}: Custom Field created")
			except Exception as e:
				failed += 1
				print(f"  [FAIL]   {dt}: Custom Field creation failed — {str(e)[:150]}")

		# Step 2: Verify the underlying DB column exists. If not, add it via raw SQL.
		# This is the critical safety net — even if Frappe doesn't sync the schema,
		# the column has to exist for our SQL queries to work.
		table_name = f"tab{dt}"
		try:
			has_col = frappe.db.sql(
				"""SELECT COUNT(*) FROM information_schema.columns
				   WHERE table_schema = DATABASE()
				     AND table_name = %s
				     AND column_name = 'catering_order'""",
				table_name,
			)[0][0] > 0

			if not has_col:
				try:
					frappe.db.sql(
						f"ALTER TABLE `{table_name}` "
						f"ADD COLUMN `catering_order` VARCHAR(140) DEFAULT NULL, "
						f"ADD INDEX `idx_catering_order` (`catering_order`)"
					)
					column_added += 1
					print(f"  [SQL]    {dt}: column added via ALTER TABLE")
				except Exception as e:
					# Index might already exist — try again without index
					try:
						frappe.db.sql(
							f"ALTER TABLE `{table_name}` "
							f"ADD COLUMN `catering_order` VARCHAR(140) DEFAULT NULL"
						)
						column_added += 1
						print(f"  [SQL]    {dt}: column added (no index)")
					except Exception as e2:
						print(f"  [FAIL]   {dt}: ALTER TABLE failed — {str(e2)[:150]}")
		except Exception as e:
			print(f"  [FAIL]   {dt}: column check failed — {str(e)[:150]}")

	frappe.db.commit()

	# Clear caches so Frappe re-reads metadata
	try:
		frappe.clear_cache()
	except Exception:
		pass

	print("")
	print(f"=== Summary ===")
	print(f"  Custom Fields created:       {added}")
	print(f"  Custom Fields already there: {already}")
	print(f"  Custom Field failures:       {failed}")
	print(f"  Columns added via SQL:       {column_added}")
	print(f"  Total doctypes processed:    {len(TARGETS)}")
	print("")
	print("Run `bench --site [site] migrate && bench --site [site] clear-cache` next.")
