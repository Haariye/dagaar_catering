import frappe


def execute():
	"""Backfill is_additional_service=1 on existing additional service SIs.

	Heuristic: an SI is additional service if it has a catering_order AND
	all its items belong to item_group='Service'.

	Safe to run multiple times.
	"""
	# Ensure column exists
	try:
		has_col = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE()
			  AND table_name = 'tabSales Invoice'
			  AND column_name = 'is_additional_service'
		""")[0][0] > 0
		if not has_col:
			frappe.db.sql(
				"ALTER TABLE `tabSales Invoice` "
				"ADD COLUMN `is_additional_service` INT(1) NOT NULL DEFAULT 0"
			)
	except Exception as e:
		print(f"Patch: ALTER failed: {str(e)[:120]}")
		return

	# Find candidate SIs
	rows = frappe.db.sql("""
		SELECT name
		FROM `tabSales Invoice`
		WHERE catering_order IS NOT NULL
		  AND catering_order != ''
		  AND IFNULL(is_additional_service, 0) = 0
	""", as_dict=True)

	tagged = 0
	for r in rows:
		groups = frappe.db.sql("""
			SELECT DISTINCT i.item_group
			FROM `tabSales Invoice Item` sii
			LEFT JOIN `tabItem` i ON i.name = sii.item_code
			WHERE sii.parent = %s
		""", r.name)
		g = [x[0] for x in groups if x[0]]
		if g and all(x == "Service" for x in g):
			frappe.db.set_value("Sales Invoice", r.name,
				"is_additional_service", 1, update_modified=False)
			tagged += 1

	frappe.db.commit()
	print(f"Patch: tagged {tagged} additional service Sales Invoice(s)")
