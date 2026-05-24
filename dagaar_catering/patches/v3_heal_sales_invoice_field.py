import frappe


def execute():
	"""Heal orders whose sales_invoice field was wrongly set to an additional
	service SI by the old linker hook.

	For each Catering Order whose sales_invoice points to a SI with
	is_additional_service=1, find the actual package SI (or None) and rewrite
	the field.
	"""
	# Make sure the column exists
	try:
		col = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE()
			  AND table_name = 'tabSales Invoice'
			  AND column_name = 'is_additional_service'
		""")[0][0]
		if not col:
			print("Patch: is_additional_service column not present yet — skipping")
			return
	except Exception as e:
		print(f"Patch: column check failed: {str(e)[:120]}")
		return

	# Find orders where sales_invoice points to a service SI
	rows = frappe.db.sql("""
		SELECT co.name, co.sales_invoice
		FROM `tabCatering Order` co
		INNER JOIN `tabSales Invoice` si ON si.name = co.sales_invoice
		WHERE IFNULL(si.is_additional_service, 0) = 1
	""", as_dict=True)

	healed = 0
	for r in rows:
		# Find the real package SI for this order (oldest non-service, non-return)
		pkg = frappe.db.sql("""
			SELECT name FROM `tabSales Invoice`
			WHERE catering_order = %s
			  AND docstatus IN (0, 1)
			  AND IFNULL(is_additional_service, 0) = 0
			  AND IFNULL(is_return, 0) = 0
			ORDER BY creation ASC
			LIMIT 1
		""", r.name)
		pkg_name = pkg[0][0] if pkg else None

		frappe.db.set_value("Catering Order", r.name, "sales_invoice",
			pkg_name, update_modified=False)
		healed += 1

	frappe.db.commit()
	print(f"Patch: healed sales_invoice on {healed} order(s)")
