# Copyright (c) 2026, DagaarSoft
# Removes the legacy "Catering Order" Workflow installed by earlier versions.
"""
The v2.x line of this app installed a Frappe Workflow on Catering Order that
gated submission via "Submit for Approval" → "Approve" → "Reject" transitions.

v3.x replaced this with a clean docstatus-based flow (Submit/Cancel) plus a
docstatus-aware status field. The old Workflow record lingers in the database
because it was never bundled as a fixture, and Frappe's Workflow engine takes
precedence over the standard Submit button when active.

This patch finds and disables any Workflow on Catering Order, plus cleans
related Workflow State / Action Master rows.
"""

import frappe


def execute():
	doctype = "Catering Order"
	print(f"Cleaning up legacy Workflow records for {doctype}...")

	# 1. Disable + delete any Workflow attached to Catering Order
	workflows = frappe.get_all("Workflow",
		filters={"document_type": doctype},
		pluck="name")
	for wf in workflows:
		try:
			print(f"  Disabling Workflow: {wf}")
			frappe.db.set_value("Workflow", wf, "is_active", 0, update_modified=False)
			frappe.delete_doc("Workflow", wf, ignore_permissions=True, force=1)
		except Exception as e:
			print(f"  Could not delete Workflow {wf}: {e}")

	# 2. Clear stale workflow_state values stored on existing Catering Order rows
	try:
		cols = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = 'tabCatering Order'
			  AND column_name = 'workflow_state'
		""")
		if cols and cols[0][0]:
			frappe.db.sql("UPDATE `tabCatering Order` SET workflow_state = NULL")
			print("  Cleared workflow_state values on existing Catering Orders")
	except Exception as e:
		print(f"  Could not clear workflow_state: {e}")

	# 3. Clean orphan Workflow State / Action Master rows
	for wfs in frappe.get_all("Workflow State",
		filters={"workflow_state_name": ["in",
			["Pending Approval", "Approved", "Rejected"]]},
		pluck="name"):
		try:
			# Only delete if no other doctype's workflow uses it
			using = frappe.db.count("Workflow Document State",
				filters={"state": wfs})
			if not using:
				frappe.delete_doc("Workflow State", wfs,
					ignore_permissions=True, force=1)
				print(f"  Removed orphan Workflow State: {wfs}")
		except Exception:
			pass

	frappe.db.commit()
	print("Done. The standard Submit/Cancel buttons should now appear.")
