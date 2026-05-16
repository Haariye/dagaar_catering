# Copyright (c) 2026, DagaarSoft
"""Delete the old 'Catering Order P&L' Report row.

The new report is 'Catering Order Profitability'.
"""

import frappe


def execute():
	old_name = "Catering Order P&L"
	if frappe.db.exists("Report", old_name):
		try:
			frappe.delete_doc("Report", old_name, ignore_permissions=True, force=1)
			print(f"  Deleted old report record: {old_name}")
		except Exception as e:
			print(f"  Could not delete {old_name}: {e}")
	else:
		print(f"  {old_name} not in DB — nothing to do")
