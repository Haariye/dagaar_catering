# Copyright (c) 2024, DagaarSoft and contributors
# License: MIT
"""
DagaarSoft Catering — Install / Migrate Hooks

KEY FIXES IN v2.0:
1. Workspace `links` use `type = "Link"` or `"Card Break"` (not "DocType"/"Report").
   The doctype/report distinction goes in the `link_type` field.
2. Custom Fields on ERPNext docs are NOT created in after_install (DocTypes don't
   exist yet at that point). They are created in a separate function called from
   after_migrate AND from a patch — both run AFTER all DocTypes are synced.
3. Every step is wrapped in its own try/except so a failure in one step does not
   abort the entire install.
4. All steps are idempotent — safe to run repeatedly.
"""
import frappe
from frappe import _
import json


# ════════════════════════════════════════════════════════════════════════════
# ENTRY POINTS
# ════════════════════════════════════════════════════════════════════════════

def after_install():
	"""Run after `bench install-app dagaar_catering`.

	At this point DocTypes from JSON have already been synced. We create roles,
	settings, workflows, workspace, and custom fields on ERPNext docs.
	Each step is independent — a failure in one does not abort the others.
	"""
	_safe_step("Roles",          create_catering_roles)
	_safe_step("Settings",       create_default_settings)
	_safe_step("Workflows",      create_catering_workflows)
	_safe_step("Workspace",      create_catering_workspace)
	_safe_step("Custom Fields",  add_catering_order_custom_fields)
	frappe.db.commit()
	print("DagaarSoft Catering: post-install setup completed.")


def after_migrate():
	"""Run after `bench migrate`. Same as after_install but idempotent."""
	_safe_step("Roles",          create_catering_roles)
	_safe_step("Settings",       create_default_settings)
	_safe_step("Workflows",      create_catering_workflows)
	_safe_step("Workspace",      create_catering_workspace)
	_safe_step("Custom Fields",  add_catering_order_custom_fields)
	frappe.db.commit()


def _safe_step(name, fn):
	"""Run a setup step; log the error but never raise."""
	try:
		fn()
		print(f"  ✓ DagaarSoft: {name}")
	except Exception as e:
		# Truncate error to avoid CharacterLengthExceededError
		msg = str(e)[:200].replace("\n", " ")
		print(f"  ✗ DagaarSoft: {name} — {msg}")


# ════════════════════════════════════════════════════════════════════════════
# ROLES
# ════════════════════════════════════════════════════════════════════════════

def create_catering_roles():
	roles = [
		"Catering Manager",
		"Catering Sales User",
		"Catering Finance User",
		"Catering Finance Manager",
		"Catering Kitchen User",
		"Catering Kitchen Manager",
		"Catering Procurement User",
		"Catering Procurement Manager",
		"Catering Delivery User",
		"Catering Auditor",
		"Catering Management",
	]
	for role_name in roles:
		if not frappe.db.exists("Role", role_name):
			role = frappe.new_doc("Role")
			role.role_name = role_name
			role.desk_access = 1
			role.insert(ignore_permissions=True)


# ════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ════════════════════════════════════════════════════════════════════════════

def create_default_settings():
	if not frappe.db.exists("DocType", "Catering Settings"):
		return
	if frappe.db.exists("Catering Settings", "Catering Settings"):
		return
	settings = frappe.new_doc("Catering Settings")
	settings.default_currency = frappe.db.get_default("currency") or "USD"
	settings.minimum_margin_percent = 15.0
	settings.default_wastage_percent = 5.0
	settings.default_deposit_percent = 30.0
	settings.require_deposit_before_production = 1
	settings.require_so_before_production = 1
	settings.require_delivery_before_invoice = 1
	settings.require_profit_review_before_closure = 1
	settings.require_invoice_before_closure = 1
	settings.auto_create_activity_log = 1
	settings.insert(ignore_permissions=True)


# ════════════════════════════════════════════════════════════════════════════
# WORKFLOWS
# ════════════════════════════════════════════════════════════════════════════

def create_catering_workflows():
	_create_order_workflow()
	_create_emergency_expense_workflow()


def _create_order_workflow():
	if frappe.db.exists("Workflow", "Catering Order Approval"):
		return
	if not frappe.db.exists("DocType", "Catering Order"):
		return
	# Check workflow_state column exists in tabCatering Order before creating workflow
	if not _column_exists("tabCatering Order", "workflow_state"):
		return

	wf = frappe.new_doc("Workflow")
	wf.workflow_name = "Catering Order Approval"
	wf.document_type = "Catering Order"
	wf.workflow_state_field = "workflow_state"
	wf.is_active = 1
	wf.override_status = 0
	wf.send_email_alert = 0

	for state, doc_status, allow_edit, style in [
		("Draft",            "0", "Catering Manager",    "Warning"),
		("Pending Approval", "0", "Catering Management", "Warning"),
		("Approved",         "0", "Catering Manager",    "Success"),
		("Rejected",         "0", "Catering Manager",    "Danger"),
	]:
		wf.append("states", {
			"state": state, "doc_status": doc_status,
			"allow_edit": allow_edit, "style": style,
		})

	for state, action, next_state, allowed, self_approval in [
		("Draft",            "Submit for Approval", "Pending Approval", "Catering Manager",    1),
		("Pending Approval", "Approve",             "Approved",         "Catering Management", 0),
		("Pending Approval", "Reject",              "Rejected",         "Catering Management", 0),
		("Rejected",         "Revise",              "Draft",            "Catering Manager",    1),
	]:
		wf.append("transitions", {
			"state": state, "action": action, "next_state": next_state,
			"allowed": allowed, "allow_self_approval": self_approval,
		})

	wf.insert(ignore_permissions=True)


def _create_emergency_expense_workflow():
	if frappe.db.exists("Workflow", "Emergency Expense Approval"):
		return
	if not frappe.db.exists("DocType", "Catering Emergency Expense"):
		return
	if not _column_exists("tabCatering Emergency Expense", "workflow_state"):
		return

	wf = frappe.new_doc("Workflow")
	wf.workflow_name = "Emergency Expense Approval"
	wf.document_type = "Catering Emergency Expense"
	wf.workflow_state_field = "workflow_state"
	wf.is_active = 1
	wf.override_status = 0
	wf.send_email_alert = 0

	for state, doc_status, allow_edit, style in [
		("Draft",                    "0", "Catering Procurement User", "Warning"),
		("Pending Manager Approval", "0", "Catering Manager",          "Warning"),
		("Approved",                 "0", "Catering Finance User",     "Success"),
		("Rejected",                 "0", "Catering Procurement User", "Danger"),
	]:
		wf.append("states", {
			"state": state, "doc_status": doc_status,
			"allow_edit": allow_edit, "style": style,
		})

	for state, action, next_state, allowed, self_approval in [
		("Draft",                    "Request Approval", "Pending Manager Approval", "Catering Procurement User", 1),
		("Pending Manager Approval", "Approve",          "Approved",                 "Catering Manager",          0),
		("Pending Manager Approval", "Reject",           "Rejected",                 "Catering Manager",          0),
		("Rejected",                 "Revise",           "Draft",                    "Catering Procurement User", 1),
	]:
		wf.append("transitions", {
			"state": state, "action": action, "next_state": next_state,
			"allowed": allowed, "allow_self_approval": self_approval,
		})

	wf.insert(ignore_permissions=True)


def _column_exists(table, column):
	"""Check if a column exists in a MariaDB table."""
	try:
		row = frappe.db.sql(f"""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE()
			  AND table_name = %s
			  AND column_name = %s
		""", (table, column))
		return row and row[0][0] > 0
	except Exception:
		return False


# ════════════════════════════════════════════════════════════════════════════
# WORKSPACE — FIXED LINK TYPES
# ════════════════════════════════════════════════════════════════════════════
#
# FIX: Frappe Workspace Link table accepts ONLY these values for `type`:
#   - "Link"
#   - "Card Break"
# The doctype/report distinction goes in the `link_type` field instead.
# Previously we used type="DocType" and type="Report" which is invalid.
# ════════════════════════════════════════════════════════════════════════════

def create_catering_workspace():
	if not frappe.db.exists("DocType", "Catering Order"):
		# Don't create workspace until DocTypes are present (otherwise links won't validate)
		return

	# If workspace from v1 exists with broken links, delete and recreate clean
	if frappe.db.exists("Workspace", "Dagaar Catering"):
		try:
			# Check if links table has any rows with bad type values
			bad_link_count = frappe.db.sql("""
				SELECT COUNT(*) FROM `tabWorkspace Link`
				WHERE parent = %s AND type NOT IN ('Link', 'Card Break')
			""", "Dagaar Catering")
			if bad_link_count and bad_link_count[0][0] > 0:
				# Has invalid link rows from v1 — delete and recreate
				frappe.delete_doc("Workspace", "Dagaar Catering", force=True, ignore_permissions=True)
				frappe.db.commit()
			else:
				return  # workspace is fine, leave it
		except Exception:
			# If we can't inspect, just leave the existing workspace alone
			return

	ws = frappe.new_doc("Workspace")
	ws.name = "Dagaar Catering"
	ws.title = "Dagaar Catering"
	ws.label = "Dagaar Catering"
	ws.module = "Catering Management"
	ws.icon = "briefcase"
	ws.is_standard = 0
	ws.public = 1

	# Workspace content: list of blocks
	ws.content = json.dumps([
		{"type": "header", "data": {"text": "<span class=\"h4\"><b>Sales &amp; Orders</b></span>", "col": 12}},
		{"type": "card",   "data": {"card_name": "Sales & Orders", "col": 4}},
		{"type": "card",   "data": {"card_name": "Menu Management", "col": 4}},
		{"type": "card",   "data": {"card_name": "Kitchen & Production", "col": 4}},
		{"type": "header", "data": {"text": "<span class=\"h4\"><b>Finance &amp; Reports</b></span>", "col": 12}},
		{"type": "card",   "data": {"card_name": "Finance", "col": 4}},
		{"type": "card",   "data": {"card_name": "Reports", "col": 8}},
		{"type": "header", "data": {"text": "<span class=\"h4\"><b>Settings</b></span>", "col": 12}},
		{"type": "card",   "data": {"card_name": "Settings", "col": 4}},
	])

	# ── Shortcuts (top of workspace) ────────────────────────────────────────
	# In the Workspace Shortcut child table, `type` IS the doctype/report kind.
	shortcuts = [
		("Catering Order",         "Catering Order",            "DocType", "#e74c3c"),
		("Cost Sheet",             "Catering Cost Sheet",       "DocType", "#2ecc71"),
		("Closing Sheet",          "Catering Closing Sheet",    "DocType", "#3498db"),
		("Production Plan",        "Catering Production Plan",  "DocType", "#f39c12"),
		("Settings",               "Catering Settings",         "DocType", "#7f8c8d"),
	]
	for label, link_to, kind, color in shortcuts:
		if kind == "DocType" and not frappe.db.exists("DocType", link_to):
			continue
		ws.append("shortcuts", {
			"label":   label,
			"link_to": link_to,
			"type":    kind,    # Workspace Shortcut accepts "DocType", "Page", "Report", "URL"
			"color":   color,
		})

	# ── Links (cards body) — uses Link / Card Break only ────────────────────
	# Link doctypes/reports go in `link_type` field, not `type`.
	links = [
		# Sales & Orders
		("Sales & Orders",                "Card Break",  "",                              ""),
		("Catering Order",                "Link",        "DocType",                       "Catering Order"),
		("Catering Cost Sheet",           "Link",        "DocType",                       "Catering Cost Sheet"),
		("Catering Closing Sheet",        "Link",        "DocType",                       "Catering Closing Sheet"),
		("Catering Delivery Plan",        "Link",        "DocType",                       "Catering Delivery Plan"),
		# Menu Management
		("Menu Management",               "Card Break",  "",                              ""),
		("Catering Menu Package",         "Link",        "DocType",                       "Catering Menu Package"),
		("Catering Recipe",               "Link",        "DocType",                       "Catering Recipe"),
		# Kitchen & Production
		("Kitchen & Production",          "Card Break",  "",                              ""),
		("Catering Production Plan",      "Link",        "DocType",                       "Catering Production Plan"),
		("Catering Wastage Entry",        "Link",        "DocType",                       "Catering Wastage Entry"),
		("Catering Return Entry",         "Link",        "DocType",                       "Catering Return Entry"),
		# Finance
		("Finance",                       "Card Break",  "",                              ""),
		("Catering Emergency Expense",    "Link",        "DocType",                       "Catering Emergency Expense"),
		("Catering Activity Log",         "Link",        "DocType",                       "Catering Activity Log"),
		# Reports
		("Reports",                       "Card Break",  "",                              ""),
		("Catering PL by Order",          "Link",        "Report",                        "Catering PL by Order"),
		("Gross Margin Analysis",         "Link",        "Report",                        "Gross Margin Analysis"),
		("Wastage Return Report",         "Link",        "Report",                        "Wastage Return Report"),
		("Catering Receivable Report",    "Link",        "Report",                        "Catering Receivable Report"),
		("Supplier Payable Report",       "Link",        "Report",                        "Supplier Payable Report"),
		("Cost Center Performance",       "Link",        "Report",                        "Cost Center Performance"),
		("Cash Flow Impact Report",       "Link",        "Report",                        "Cash Flow Impact Report"),
		("Balance Sheet Impact",          "Link",        "Report",                        "Balance Sheet Impact"),
		("Food BOM Cost Variance",        "Link",        "Report",                        "Food BOM Cost Variance"),
		("Management KPI Report",         "Link",        "Report",                        "Management KPI Report"),
		# Settings
		("Settings",                      "Card Break",  "",                              ""),
		("Catering Settings",             "Link",        "DocType",                       "Catering Settings"),
	]

	for label, link_kind, link_type, link_to in links:
		# Skip links pointing at non-existent DocTypes/Reports — avoids LinkValidationError
		if link_kind == "Link":
			if link_type == "DocType" and not frappe.db.exists("DocType", link_to):
				continue
			if link_type == "Report" and not frappe.db.exists("Report", link_to):
				continue

		row = {
			"label":     label,
			"type":      link_kind,    # "Link" or "Card Break" only
		}
		if link_kind == "Link":
			row["link_type"]      = link_type    # "DocType", "Report", "Page", or "URL"
			row["link_to"]        = link_to
			row["is_query_report"] = 1 if link_type == "Report" else 0
		ws.append("links", row)

	ws.insert(ignore_permissions=True, ignore_links=True)


# ════════════════════════════════════════════════════════════════════════════
# CUSTOM FIELDS ON ERPNEXT DOCUMENTS
# ════════════════════════════════════════════════════════════════════════════
#
# FIX: This function checks that the Catering Order DocType actually exists
# in the database (table tabCatering Order) before adding Link fields to it.
# Previously it ran during after_install BEFORE doctypes were synced, so
# `Catering Order` didn't exist yet → "Options must be a valid DocType" error.
# ════════════════════════════════════════════════════════════════════════════

def add_catering_order_custom_fields():
	"""Add catering_order link to ERPNext docs.

	Strategy (works even when Frappe Custom Field API rejects the field):
	  1. Add the column directly via ALTER TABLE (always succeeds in MariaDB)
	  2. Add the Custom Field metadata so it shows in the UI
	Both steps are idempotent — safe to run multiple times.
	"""
	if not frappe.db.exists("DocType", "Catering Order"):
		print("    Catering Order DocType not yet installed — skipping.")
		return

	targets = [
		"Quotation", "Sales Order", "Sales Invoice", "Payment Entry",
		"Journal Entry", "Delivery Note", "Material Request",
		"Purchase Order", "Purchase Invoice", "Work Order", "Stock Entry",
	]

	columns_added = 0
	fields_added = 0
	skipped = 0

	for dt in targets:
		if not frappe.db.exists("DocType", dt):
			skipped += 1
			continue

		table = f"tab{dt}"

		# Step 1: ALTER TABLE if the column is missing.
		# This is the critical step — the database column MUST exist for SQL queries.
		try:
			has_col = frappe.db.sql(
				"""SELECT COUNT(*) FROM information_schema.columns
				   WHERE table_schema = DATABASE()
				     AND table_name = %s
				     AND column_name = 'catering_order'""",
				table,
			)[0][0] > 0
			if not has_col:
				frappe.db.sql(
					f"ALTER TABLE `{table}` "
					f"ADD COLUMN `catering_order` VARCHAR(140) DEFAULT NULL"
				)
				# Best-effort index for performance
				try:
					frappe.db.sql(
						f"ALTER TABLE `{table}` ADD INDEX `idx_catering_order` (`catering_order`)"
					)
				except Exception:
					pass  # index may already exist
				columns_added += 1
		except Exception as e:
			print(f"    [{dt}] ALTER TABLE failed: {str(e)[:120]}")

		# Step 2: Create Custom Field metadata so the field is editable in the UI.
		# This may fail if Frappe validation rejects it — that's fine, the column already exists.
		cf_name = f"{dt}-catering_order"
		if not frappe.db.exists("Custom Field", cf_name):
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
				cf.flags.ignore_validate = True
				cf.flags.ignore_permissions = True
				cf.insert(ignore_permissions=True)
				fields_added += 1
			except Exception:
				pass

	frappe.db.commit()
	print(f"    catering_order: {columns_added} columns added, {fields_added} custom fields added, {skipped} doctypes skipped")



def _table_exists(table_name):
	try:
		row = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.tables
			WHERE table_schema = DATABASE() AND table_name = %s
		""", table_name)
		return row and row[0][0] > 0
	except Exception:
		return False


# ════════════════════════════════════════════════════════════════════════════
# MANUAL DOCTYPE SYNC — fallback if Frappe's auto-sync didn't pick up our doctypes
# ════════════════════════════════════════════════════════════════════════════

def force_sync_doctypes():
	"""Manually sync all DocType JSON files from the catering_management module folder.

	Use this as a recovery tool when `bench migrate` doesn't auto-sync our DocTypes:
	    bench --site uat.dagaartech.com execute \\
	        dagaar_catering.catering_management.setup.install.force_sync_doctypes
	"""
	import os
	from frappe.modules.import_file import import_file_by_path

	app_path = frappe.get_app_path("dagaar_catering")
	# Path to our module folder
	module_path = os.path.join(app_path, "catering_management")

	if not os.path.exists(module_path):
		print(f"ERROR: Module folder not found at {module_path}")
		print(f"App path resolved to: {app_path}")
		return

	# Sync DocTypes
	doctype_dir = os.path.join(module_path, "doctype")
	if os.path.exists(doctype_dir):
		count = 0
		for d in sorted(os.listdir(doctype_dir)):
			folder = os.path.join(doctype_dir, d)
			if not os.path.isdir(folder):
				continue
			json_file = os.path.join(folder, f"{d}.json")
			if os.path.exists(json_file):
				try:
					import_file_by_path(json_file, force=True, ignore_version=True, reset_permissions=True)
					count += 1
				except Exception as e:
					print(f"  Failed to sync {d}: {str(e)[:150]}")
		print(f"Synced {count} DocTypes from {doctype_dir}")

	# Sync Reports
	report_dir = os.path.join(module_path, "report")
	if os.path.exists(report_dir):
		count = 0
		for r in sorted(os.listdir(report_dir)):
			folder = os.path.join(report_dir, r)
			if not os.path.isdir(folder):
				continue
			json_file = os.path.join(folder, f"{r}.json")
			if os.path.exists(json_file):
				try:
					import_file_by_path(json_file, force=True, ignore_version=True)
					count += 1
				except Exception as e:
					print(f"  Failed to sync report {r}: {str(e)[:150]}")
		print(f"Synced {count} Reports from {report_dir}")

	frappe.db.commit()
	print("Force sync complete. Now run after_migrate to set up workspace and custom fields:")
	print("  bench --site [site] execute dagaar_catering.catering_management.setup.install.after_migrate")


def diagnose():
	"""Diagnostic function to help understand why DocTypes aren't being installed.

	Run with:
	    bench --site uat.dagaartech.com execute \\
	        dagaar_catering.catering_management.setup.install.diagnose
	"""
	import os

	print("\n=== DagaarSoft Catering Diagnostic ===\n")

	# Check app is registered
	installed = frappe.get_installed_apps()
	print(f"1. Is 'dagaar_catering' in installed apps? {'YES' if 'dagaar_catering' in installed else 'NO'}")
	print(f"   All installed apps: {installed}")

	# Resolve app path
	try:
		app_path = frappe.get_app_path("dagaar_catering")
		print(f"\n2. App Python package path: {app_path}")
	except Exception as e:
		print(f"\n2. ERROR resolving app path: {e}")
		return

	# Check modules.txt
	modules_txt = os.path.join(app_path, "modules.txt")
	if os.path.exists(modules_txt):
		with open(modules_txt) as f:
			modules = [m.strip() for m in f.readlines() if m.strip()]
		print(f"\n3. modules.txt contents: {modules}")
	else:
		print(f"\n3. ERROR: modules.txt not found at {modules_txt}")
		return

	# Check module folder
	for module in modules:
		scrubbed = module.lower().replace(" ", "_").replace("-", "_")
		module_path = os.path.join(app_path, scrubbed)
		print(f"\n4. Module '{module}' (scrubbed='{scrubbed}'):")
		print(f"   Expected folder: {module_path}")
		print(f"   Exists: {os.path.exists(module_path)}")
		if os.path.exists(module_path):
			print(f"   Contents: {os.listdir(module_path)}")
			doctype_path = os.path.join(module_path, "doctype")
			if os.path.exists(doctype_path):
				doctypes = [d for d in os.listdir(doctype_path)
							if os.path.isdir(os.path.join(doctype_path, d))]
				print(f"   DocType folders found: {len(doctypes)}")
				print(f"   Sample: {doctypes[:5]}")
			else:
				print(f"   ERROR: doctype folder not found at {doctype_path}")

	# Check what DocTypes Frappe knows about
	count = frappe.db.count("DocType", {"module": "Catering Management"})
	print(f"\n5. DocTypes in DB with module='Catering Management': {count}")

	# Check Catering Order specifically
	exists = frappe.db.exists("DocType", "Catering Order")
	print(f"\n6. Does 'Catering Order' DocType exist in DB? {'YES' if exists else 'NO'}")
	if exists:
		# Check the underlying table
		table_exists = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.tables
			WHERE table_schema = DATABASE() AND table_name = 'tabCatering Order'
		""")[0][0] > 0
		print(f"   Underlying table 'tabCatering Order' exists: {'YES' if table_exists else 'NO'}")

	# Check Module Def
	mdef = frappe.db.exists("Module Def", "Catering Management")
	print(f"\n7. Module Def 'Catering Management' registered? {'YES' if mdef else 'NO'}")

	print("\n=== End Diagnostic ===\n")
