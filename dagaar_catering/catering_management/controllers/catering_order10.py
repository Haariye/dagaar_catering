# Copyright (c) 2024, DagaarSoft and contributors
# License: MIT
"""
Catering Order Controller — Enterprise v2.0

Workflow-driven, context-aware, auto-fetching orchestration.
The Catering Order is the central control hub. This controller:
- Auto-fetches defaults from Catering Settings, Company, Customer, Menu Package
- Auto-loads menu items when a Menu Package is selected
- Auto-calculates all totals based on guest count and items
- Validates business rules (margin, event date, guest count)
- Provides whitelisted methods to create every linked document
- Each create_* method auto-fills customer, items, accounts, taxes, cost center
"""
import frappe
from frappe import _
from frappe.utils import flt, getdate, today, add_days, now_datetime, get_link_to_form, cint




# ════════════════════════════════════════════════════════════════════════════
# DEFENSIVE SQL HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _safe_sum_with_catering_link(table_name, column, catering_order, extra_where=""):
	"""SUM a column from an ERPNext table where catering_order matches.

	Returns 0 silently if the catering_order column doesn't exist on that table
	(e.g., if the Custom Field patch hasn't run yet).
	"""
	try:
		# Check column exists first
		col_exists = frappe.db.sql(
			"""SELECT COUNT(*) FROM information_schema.columns
			   WHERE table_schema = DATABASE()
			     AND table_name = %s
			     AND column_name = 'catering_order'""",
			table_name,
		)[0][0] > 0
		if not col_exists:
			return 0
		where = f"docstatus = 1 AND catering_order = %s {extra_where}"
		row = frappe.db.sql(
			f"SELECT IFNULL(SUM({column}), 0) FROM `{table_name}` WHERE {where}",
			catering_order,
		)
		return flt(row[0][0]) if row else 0
	except Exception:
		return 0


# ════════════════════════════════════════════════════════════════════════════
# CHANGE-DETECTION SNAPSHOT (for auto-cancel-and-regenerate on guest/item change)
# ════════════════════════════════════════════════════════════════════════════

def _compute_order_snapshot(doc):
	"""Compute a hash representing the billable contents of the order.

	This hash captures: total_guests + each item's (item_code, total_qty, rate).
	If this hash changes after a Sales Invoice was created, the previous billing is
	stale and must be re-issued.
	"""
	import hashlib
	items_str = ""
	for it in (doc.items or []):
		items_str += f"|{it.item_code}:{flt(it.total_qty):.3f}:{flt(it.rate):.4f}"
	snapshot_str = f"guests={flt(doc.total_guests):.0f}{items_str}"
	return hashlib.md5(snapshot_str.encode()).hexdigest()[:16]


def _check_for_rebilling_required(doc):
	"""If the order has changed AFTER a Sales Invoice was created, mark for rebill.

	Called during validate(). Two outcomes:
	- If no Sales Invoice yet: just update the snapshot, no flag.
	- If Sales Invoice exists and snapshot changed: set requires_rebill = 1 and warn user.
	"""
	current_snapshot = _compute_order_snapshot(doc)

	if not doc.get("sales_invoice"):
		# Not yet billed — just track the current state
		doc.last_billed_snapshot = current_snapshot
		doc.requires_rebill = 0
		return

	# Sales Invoice exists. Has it changed since?
	if doc.last_billed_snapshot and doc.last_billed_snapshot != current_snapshot:
		doc.requires_rebill = 1
		frappe.msgprint(
			_("⚠️ Order changed after billing. Sales Order and Invoice are now stale. "
			  "Click 'Regenerate Bill' to cancel them and create new ones."),
			indicator="orange", alert=True, title=_("Rebill Required")
		)
	else:
		doc.requires_rebill = 0




# ════════════════════════════════════════════════════════════════════════════
# DOCUMENT LIFECYCLE HOOKS
# ════════════════════════════════════════════════════════════════════════════

def validate(doc, method=None):
	_load_settings_defaults(doc)
	_calculate_totals(doc)
	_compute_balance(doc)
	_validate_event_date(doc)
	_validate_guests(doc)
	_validate_margin_warning(doc)
	_set_status(doc)
	_set_title(doc)
	_check_for_rebilling_required(doc)


def before_save(doc, method=None):
	if not doc.get("created_by_name"):
		doc.created_by_name = frappe.utils.get_fullname(frappe.session.user)
	if doc.customer and not doc.contact_email:
		_fetch_primary_contact(doc)


def after_insert(doc, method=None):
	_log_activity(doc, "Order Created", f"Catering Order created for {doc.customer_name or doc.customer}")
	if doc.menu_package and not doc.items:
		_load_menu_package(doc)


def on_update(doc, method=None):
	prev = doc.get_doc_before_save()
	if prev and prev.menu_package != doc.menu_package and doc.menu_package:
		_load_menu_package(doc)


def on_submit(doc, method=None):
	_log_activity(doc, "Document Submitted", f"Catering Order {doc.name} submitted for processing")
	if doc.status in ("Draft", None, ""):
		frappe.db.set_value(doc.doctype, doc.name, "status", "Confirmed")




def on_cancel(doc, method=None):
	"""When Catering Order is cancelled, force-cancel all linked documents.

	Order of cancellation (reverse dependency):
	  1. Closing Sheet
	  2. Delivery Note(s)
	  3. Delivery Plan
	  4. Material Request(s)
	  5. Work Order(s) under Production Plan
	  6. Production Plan
	  7. Cost Sheet (with its JE)
	  8. Wastage Entries (with their JE and Stock Entry)
	  9. Emergency Expenses (with their JE)
	  10. Payment Entry(s) — reverses GL postings
	  11. Sales Invoice(s) — supplementary first, then primary
	  12. Sales Order

	All cancellations use ignore_permissions and ignore_link checks.
	"""
	catering_order = doc.name

	def _force_cancel(doctype, name, reason="parent order cancelled"):
		"""Cancel a single doc, swallowing all errors."""
		if not name:
			return
		try:
			d = frappe.get_doc(doctype, name)
			if d.docstatus == 1:
				d.flags.ignore_permissions = True
				d.flags.ignore_links = True
				d.flags.ignore_on_trash = True
				d.cancel()
		except Exception as e:
			frappe.log_error(f"Cascade cancel failed: {doctype} {name}: {str(e)[:200]}",
				"Catering Order Cancel Cascade")

	# 1. Closing Sheet
	cs = frappe.db.get_value("Catering Closing Sheet",
		{"catering_order": catering_order, "docstatus": 1}, "name")
	_force_cancel("Catering Closing Sheet", cs)

	# 2. Delivery Notes (via catering_order link)
	dns = frappe.db.sql("""SELECT name FROM `tabDelivery Note`
		WHERE catering_order = %s AND docstatus = 1""", catering_order, as_dict=True)
	for row in dns:
		_force_cancel("Delivery Note", row.name)

	# 3. Delivery Plan
	dp = frappe.db.get_value("Catering Delivery Plan",
		{"catering_order": catering_order, "docstatus": 1}, "name")
	_force_cancel("Catering Delivery Plan", dp)

	# 4. Material Requests
	mrs = frappe.db.sql("""SELECT name FROM `tabMaterial Request`
		WHERE catering_order = %s AND docstatus = 1""", catering_order, as_dict=True)
	for row in mrs:
		_force_cancel("Material Request", row.name)

	# 5. Work Orders under Production Plan
	pp_name = frappe.db.get_value("Catering Production Plan",
		{"catering_order": catering_order, "docstatus": 1}, "name")
	if pp_name:
		wos = frappe.db.sql("""SELECT name FROM `tabWork Order`
			WHERE catering_order = %s AND docstatus = 1""", catering_order, as_dict=True)
		for row in wos:
			# Cancel any Stock Entries first
			ses = frappe.db.sql("""SELECT name FROM `tabStock Entry`
				WHERE work_order = %s AND docstatus = 1""", row.name, as_dict=True)
			for se in ses:
				_force_cancel("Stock Entry", se.name)
			_force_cancel("Work Order", row.name)
		# 6. Production Plan itself
		_force_cancel("Catering Production Plan", pp_name)

	# 7. Cost Sheet (its on_cancel hook will cancel the JE)
	cost_sheet = frappe.db.get_value("Catering Cost Sheet",
		{"catering_order": catering_order, "docstatus": 1}, "name")
	_force_cancel("Catering Cost Sheet", cost_sheet)

	# 8. Wastage Entries
	wastages = frappe.db.sql("""SELECT name FROM `tabCatering Wastage Entry`
		WHERE catering_order = %s AND docstatus = 1""", catering_order, as_dict=True)
	for row in wastages:
		_force_cancel("Catering Wastage Entry", row.name)

	# 9. Emergency Expenses
	emrgs = frappe.db.sql("""SELECT name FROM `tabCatering Emergency Expense`
		WHERE catering_order = %s AND docstatus = 1""", catering_order, as_dict=True)
	for row in emrgs:
		_force_cancel("Catering Emergency Expense", row.name)

	# 10. Payment Entries (direct + via Sales Invoice references)
	si_names = frappe.db.sql_list("""SELECT name FROM `tabSales Invoice`
		WHERE catering_order = %s""", catering_order)
	pe_set = set()
	# Direct PEs
	for row in frappe.db.sql("""SELECT name FROM `tabPayment Entry`
		WHERE catering_order = %s AND docstatus = 1""", catering_order, as_dict=True):
		pe_set.add(row.name)
	# PEs that reference any of our Sales Invoices
	for si in si_names:
		for row in frappe.db.sql("""SELECT DISTINCT pe.name FROM `tabPayment Entry` pe
			INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
			WHERE per.reference_doctype = 'Sales Invoice' AND per.reference_name = %s
			  AND pe.docstatus = 1""", si, as_dict=True):
			pe_set.add(row.name)
	for pe_name in pe_set:
		_force_cancel("Payment Entry", pe_name)

	# 11. Sales Invoices — cancel credit notes / supplementary first (those with is_return),
	#     then primary invoices
	sis_return_first = frappe.db.sql("""SELECT name FROM `tabSales Invoice`
		WHERE catering_order = %s AND docstatus = 1
		ORDER BY is_return DESC, creation DESC""", catering_order, as_dict=True)
	for row in sis_return_first:
		_force_cancel("Sales Invoice", row.name)

	# 12. Sales Order
	so_name = frappe.db.get_value("Sales Order",
		{"catering_order": catering_order, "docstatus": 1}, "name")
	_force_cancel("Sales Order", so_name)

	# Log the cascade
	frappe.db.set_value("Catering Order", catering_order, "status", "Cancelled", update_modified=False)
	_log_activity(doc, "Document Cancelled", f"Cascade cancel: order and all linked documents")
	frappe.msgprint(_("Catering Order cancelled — all linked documents force-cancelled."),
		indicator="red", alert=True)

# ════════════════════════════════════════════════════════════════════════════
# AUTO-FETCHING
# ════════════════════════════════════════════════════════════════════════════

def _load_settings_defaults(doc):
	settings = _get_settings()
	if settings:
		_fetch_if_blank(doc, "company", settings.default_company)
		_fetch_if_blank(doc, "branch", settings.default_branch)
		_fetch_if_blank(doc, "cost_center", settings.default_cost_center)
		_fetch_if_blank(doc, "project", settings.default_project)
		_fetch_if_blank(doc, "currency", settings.default_currency)
		_fetch_if_blank(doc, "price_list", settings.default_price_list)
		_fetch_if_blank(doc, "tax_template", settings.default_sales_tax_template)
		_fetch_if_blank(doc, "income_account", settings.default_income_account)
		_fetch_if_blank(doc, "advance_account", settings.default_advance_account)
		_fetch_if_blank(doc, "receivable_account", settings.default_receivable_account)
		_fetch_if_blank(doc, "deposit_percent", settings.default_deposit_percent)

	if doc.company:
		try:
			company = frappe.get_cached_doc("Company", doc.company)
			_fetch_if_blank(doc, "currency", company.default_currency)
			_fetch_if_blank(doc, "income_account", company.default_income_account)
			_fetch_if_blank(doc, "cost_center", company.cost_center)
		except Exception:
			pass


def _fetch_if_blank(doc, field, value):
	if value and not doc.get(field):
		doc.set(field, value)


def _get_settings():
	if not frappe.db.exists("Catering Settings", "Catering Settings"):
		return None
	try:
		return frappe.get_cached_doc("Catering Settings", "Catering Settings")
	except Exception:
		return None


def _fetch_primary_contact(doc):
	try:
		contact = frappe.db.sql("""
			SELECT c.name, c.email_id, c.mobile_no
			FROM `tabContact` c
			INNER JOIN `tabDynamic Link` dl ON dl.parent = c.name
			WHERE dl.link_doctype = 'Customer' AND dl.link_name = %s
			  AND c.is_primary_contact = 1
			LIMIT 1
		""", doc.customer, as_dict=True)
		if contact:
			doc.contact_person = contact[0].name
			doc.contact_email = contact[0].email_id
			doc.contact_mobile = contact[0].mobile_no
	except Exception:
		pass


def _load_menu_package(doc):
	if not doc.menu_package:
		return
	try:
		pkg = frappe.get_doc("Catering Menu Package", doc.menu_package)
	except frappe.DoesNotExistError:
		return

	if pkg.price_per_guest and not doc.price_per_guest:
		doc.price_per_guest = pkg.price_per_guest
	if pkg.default_currency and not doc.currency:
		doc.currency = pkg.default_currency

	if doc.items:
		return  # don't auto-load if items already exist

	for pi in (pkg.items or []):
		doc.append("items", {
			"item_code": pi.item_code,
			"item_name": pi.item_name,
			"category": pi.category,
			"qty_per_guest": pi.qty_per_guest,
			"uom": pi.uom,
			"rate": pi.rate or 0,
			"bom": pi.bom,
			"is_manufactured": pi.is_manufactured,
			"wastage_percent": pi.wastage_percent or 5,
			"guest_count": doc.total_guests,
			"menu_package_item": pi.name,
			"currency": doc.currency,
		})

	frappe.msgprint(_("Loaded {0} items from Menu Package {1}").format(
		len(pkg.items or []), pkg.package_name), indicator="blue", alert=True)


# ════════════════════════════════════════════════════════════════════════════
# CALCULATIONS
# ════════════════════════════════════════════════════════════════════════════

def _calculate_totals(doc):
	total_guests = flt(doc.total_guests) or 1

	for item in (doc.items or []):
		gc = flt(item.guest_count) or total_guests
		item.guest_count = gc
		item.total_qty = flt(item.qty_per_guest) * gc
		item.amount = flt(item.total_qty) * flt(item.rate)
		base_cost = flt(item.raw_material_cost) + flt(item.labor_cost_per_unit) * flt(item.total_qty)
		wastage_factor = 1 + (flt(item.wastage_percent) / 100)
		item.total_cost = base_cost * wastage_factor
		item.currency = doc.currency

	for gt in (doc.guest_types or []):
		gt.amount = flt(gt.guest_count) * flt(gt.rate_per_guest)
		gt.currency = doc.currency

	subtotal = sum(flt(i.amount) for i in (doc.items or []))
	subtotal += sum(flt(g.amount) for g in (doc.guest_types or []))
	doc.subtotal = subtotal

	doc.discount_amount = subtotal * flt(doc.discount_percent) / 100
	after_discount = subtotal - doc.discount_amount

	doc.total_taxes = doc.total_taxes or 0
	doc.total_order_value = after_discount + flt(doc.total_taxes)

	doc.deposit_amount = flt(doc.total_order_value) * flt(doc.deposit_percent) / 100


def _compute_balance(doc):
	doc.balance_due = flt(doc.total_order_value) - flt(doc.total_paid)


# ════════════════════════════════════════════════════════════════════════════
# VALIDATIONS
# ════════════════════════════════════════════════════════════════════════════

def _validate_event_date(doc):
	if doc.event_date and getdate(doc.event_date) < getdate(today()):
		if doc.is_new():
			frappe.msgprint(_("Event date {0} is in the past.").format(doc.event_date),
				indicator="orange", alert=True)
	if doc.event_end_date and doc.event_date:
		if getdate(doc.event_end_date) < getdate(doc.event_date):
			frappe.throw(_("Event End Date cannot be before Event Date."))


def _validate_guests(doc):
	if flt(doc.total_guests) <= 0:
		frappe.throw(_("Total Guests must be greater than 0."))
	if doc.menu_package:
		try:
			pkg = frappe.get_cached_doc("Catering Menu Package", doc.menu_package)
			if pkg.min_guests and doc.total_guests < pkg.min_guests:
				frappe.msgprint(_("Total Guests {0} is below minimum {1} for package {2}").format(
					doc.total_guests, pkg.min_guests, pkg.package_name),
					indicator="orange", alert=True)
			if pkg.max_guests and doc.total_guests > pkg.max_guests:
				frappe.msgprint(_("Total Guests {0} exceeds maximum {1} for package {2}").format(
					doc.total_guests, pkg.max_guests, pkg.package_name),
					indicator="orange", alert=True)
		except Exception:
			pass


def _validate_margin_warning(doc):
	if not flt(doc.total_order_value):
		return
	settings = _get_settings()
	if not settings or not flt(settings.minimum_margin_percent):
		return
	if doc.cost_sheet:
		try:
			total_cost = flt(frappe.db.get_value("Catering Cost Sheet", doc.cost_sheet, "total_cost"))
			if total_cost:
				margin = (flt(doc.total_order_value) - total_cost) / flt(doc.total_order_value) * 100
				if margin < flt(settings.minimum_margin_percent):
					frappe.msgprint(_("Gross Margin {0}% is below minimum of {1}%").format(
						round(margin, 1), settings.minimum_margin_percent),
						indicator="red", alert=True)
		except Exception:
			pass


def _set_status(doc):
	"""Auto-set the operational status based on linked-doc state and approval.

	Status precedence (first match wins):
	  1. Cancelled / Closed (terminal — never change)
	  2. Approval state checks:
	     - Rejected → Cancelled
	     - Pending Approval → Pending Approval
	     - Draft (not yet submitted for approval) → Draft
	  3. Operational state (only if Approved):
	     - sales_invoice exists & outstanding <= 0 → Paid
	     - sales_invoice exists → Invoiced
	     - delivery_note exists → Delivered
	     - delivery_plan complete → Delivered
	     - production_plan exists → In Production
	     - sales_order exists → Confirmed
	     - else → Approved (idle, ready to start)
	"""
	# Terminal states
	if doc.status in ("Cancelled", "Closed"):
		return

	# Approval gating
	approval = doc.get("approval_status") or "Draft"
	if approval == "Rejected":
		doc.status = "Cancelled"
		return
	if approval == "Pending Approval":
		doc.status = "Pending Approval"
		return
	if approval == "Draft":
		doc.status = "Draft"
		return

	# Approved — proceed to compute operational status
	if doc.sales_invoice:
		try:
			outstanding = flt(frappe.db.get_value("Sales Invoice", doc.sales_invoice, "outstanding_amount"))
			doc.status = "Paid" if outstanding <= 0 else "Invoiced"
			return
		except Exception:
			pass

	if doc.delivery_note:
		doc.status = "Delivered"
		return

	if doc.delivery_plan:
		try:
			dp_status = frappe.db.get_value("Catering Delivery Plan", doc.delivery_plan, "status")
			if dp_status == "Delivered":
				doc.status = "Delivered"
				return
		except Exception:
			pass

	if doc.production_plan:
		try:
			pp_status = frappe.db.get_value("Catering Production Plan", doc.production_plan, "status")
			doc.status = "Ready to Deliver" if pp_status == "Completed" else "In Production"
			return
		except Exception:
			pass

	if doc.sales_order:
		doc.status = "Confirmed"
		return

	# Approved but no operational documents yet
	doc.status = "Approved"




def _set_title(doc):
	parts = [doc.event_type or "Event", doc.customer_name or doc.customer or "", str(doc.event_date or "")]
	doc.title = " - ".join(p for p in parts if p)


# ════════════════════════════════════════════════════════════════════════════
# ACTIVITY LOGGING
# ════════════════════════════════════════════════════════════════════════════

def _log_activity(doc, activity_type, description, ref_dt=None, ref_name=None):
	settings = _get_settings()
	if settings and not cint(settings.auto_create_activity_log):
		return
	try:
		log = frappe.new_doc("Catering Activity Log")
		log.catering_order = doc.name
		log.activity_date = now_datetime()
		log.activity_type = activity_type
		log.user = frappe.session.user
		log.description = description
		log.reference_doctype = ref_dt
		log.reference_name = ref_name
		log.insert(ignore_permissions=True)
	except Exception:
		pass







def _set_naming_series(target_doc, doctype):
	"""Set naming_series on the target document.

	Strategy: try to use whatever naming_series the user has configured
	on their site. If the doctype's naming_series field has options, use the
	first one. Otherwise fall back to a sensible default that matches ERPNext v15.

	If even that fails (doctype rejects the value), we set autoname=hash so
	Frappe generates a unique name automatically.
	"""
	if not target_doc:
		return

	try:
		meta = frappe.get_meta(doctype)
		field = meta.get_field("naming_series")
	except Exception:
		field = None

	# If the doctype has no naming_series field, nothing to set
	if not field:
		return

	# Step 1: Get options from the doctype field
	series_to_try = []
	if field.options:
		series_to_try = [x.strip() for x in field.options.split("\n") if x.strip()]

	# Step 2: If no options on the field, look in `tabSeries` for any series
	# that has actually been used for this doctype (i.e., already issued names)
	if not series_to_try:
		try:
			# Search tabSeries for any prefix that has been used before
			used = frappe.db.sql(
				"""SELECT name, current FROM tabSeries
				   WHERE current > 0 LIMIT 5"""
			)
			# We don't know which is for which doctype, so just collect prefixes
			# that look reasonable
		except Exception:
			pass

	# Step 3: Hardcoded ERPNext v15 defaults as last resort
	if not series_to_try:
		fallbacks = {
			"Quotation":        "QTN-.YYYY.-",
			"Sales Order":      "SAL-ORD-.YYYY.-",
			"Sales Invoice":    "ACC-SINV-.YYYY.-",
			"Payment Entry":    "ACC-PAY-.YYYY.-",
			"Journal Entry":    "ACC-JV-.YYYY.-",
			"Delivery Note":    "MAT-DN-.YYYY.-",
			"Material Request": "MAT-MR-.YYYY.-",
			"Purchase Order":   "PUR-ORD-.YYYY.-",
			"Purchase Invoice": "ACC-PINV-.YYYY.-",
			"Work Order":       "MFG-WO-.YYYY.-",
			"Stock Entry":      "MAT-STE-.YYYY.-",
		}
		if doctype in fallbacks:
			series_to_try = [fallbacks[doctype]]

	# Set the first available series
	if series_to_try:
		target_doc.naming_series = series_to_try[0]


def _ensure_naming_series_option(doctype, series):
	"""Add `series` to the naming_series field options if not already there.

	This makes the value valid for the doctype, so Frappe accepts it on insert.
	Uses Property Setter so the change is permanent.
	"""
	try:
		meta = frappe.get_meta(doctype)
		field = meta.get_field("naming_series")
		if not field:
			return
		current_options = (field.options or "").split("\n")
		current_options = [x.strip() for x in current_options if x.strip()]
		if series in current_options:
			return  # already there

		# Append the series to the options list
		new_options = current_options + [series]
		new_options_str = "\n".join(new_options)

		# Use Property Setter to persist the change
		ps_name = f"{doctype}-naming_series-options"
		if frappe.db.exists("Property Setter", ps_name):
			frappe.db.set_value("Property Setter", ps_name, "value", new_options_str)
		else:
			ps = frappe.new_doc("Property Setter")
			ps.doctype_or_field = "DocField"
			ps.doc_type = doctype
			ps.field_name = "naming_series"
			ps.property = "options"
			ps.value = new_options_str
			ps.property_type = "Text"
			ps.flags.ignore_permissions = True
			ps.insert(ignore_permissions=True)
		# Clear cache so the change is picked up
		frappe.clear_cache(doctype=doctype)
	except Exception:
		pass


def _create_doc_with_naming(doctype):
	"""Create a new doc and ensure its naming_series is set to a valid value.

	Combines _set_naming_series + _ensure_naming_series_option for bulletproof creation.
	"""
	doc = frappe.new_doc(doctype)
	# Step 1: pick a series
	_set_naming_series(doc, doctype)
	# Step 2: make sure that series is in the doctype's options
	if doc.get("naming_series"):
		_ensure_naming_series_option(doctype, doc.naming_series)
	return doc


# ════════════════════════════════════════════════════════════════════════════
# WHITELISTED ACTIONS
# ════════════════════════════════════════════════════════════════════════════


@frappe.whitelist()
def create_sales_order(catering_order, auto_submit=0):
	"""Create Sales Order from Catering Order."""
	co = frappe.get_doc("Catering Order", catering_order)
	_check_approval_gate(co)
	if co.sales_order:
		frappe.throw(_("Sales Order {0} already exists.").format(co.sales_order))
	if not co.items:
		frappe.throw(_("Add items before creating a Sales Order."))

	so = _create_doc_with_naming("Sales Order")
	so.customer = co.customer
	so.transaction_date = today()
	so.delivery_date = co.event_date
	so.company = co.company
	so.cost_center = co.cost_center
	so.project = co.project
	so.currency = co.currency
	so.conversion_rate = flt(co.conversion_rate) or 1
	so.selling_price_list = co.price_list
	so.catering_order = co.name

	for it in co.items:
		so.append("items", {
			"item_code": it.item_code,
			"item_name": it.item_name,
			"qty": flt(it.total_qty) or 1,
			"rate": flt(it.rate),
			"uom": it.uom,
			"delivery_date": co.event_date,
			"cost_center": co.cost_center,
		})

	if co.tax_template:
		so.taxes_and_charges = co.tax_template
		_apply_tax_template(so, co.tax_template, "Sales Taxes and Charges Template")

	so.flags.ignore_permissions = True
	so.insert()

	if cint(auto_submit):
		try:
			so.submit()
		except Exception as e:
			frappe.msgprint(_("Sales Order {0} created but auto-submit failed: {1}").format(
				so.name, str(e)[:200]), indicator="orange")

	frappe.db.set_value("Catering Order", catering_order, {"sales_order": so.name, "status": "Confirmed"})
	_log_activity(co, "Document Created", f"Sales Order {so.name} created", "Sales Order", so.name)
	frappe.msgprint(_("Sales Order {0} {1}").format(
		get_link_to_form("Sales Order", so.name),
		"created and submitted" if cint(auto_submit) else "created"
	), indicator="green", alert=True)
	return so.name


@frappe.whitelist()
def get_sales_invoice_defaults(catering_order):
	"""Return defaults for the Sales Invoice popup dialog."""
	co = frappe.get_doc("Catering Order", catering_order)
	if co.sales_invoice:
		return {"error": f"Sales Invoice {co.sales_invoice} already exists for this order."}
	if not co.sales_order:
		return {"error": "Create a Sales Order first."}

	return {
		"customer": co.customer,
		"customer_name": co.customer_name,
		"posting_date": today(),
		"due_date": add_days(today(), 30),
		"company": co.company,
		"currency": co.currency,
		"grand_total": flt(co.total_order_value),
		"suggested_discount": 0,
	}


@frappe.whitelist()
def create_sales_invoice(catering_order, additional_discount=None, auto_submit=0):
	"""Create Sales Invoice with optional additional discount."""
	co = frappe.get_doc("Catering Order", catering_order)
	_check_approval_gate(co)
	if co.sales_invoice:
		frappe.throw(_("Sales Invoice {0} already exists.").format(co.sales_invoice))

	si = _create_doc_with_naming("Sales Invoice")
	si.customer = co.customer
	si.posting_date = today()
	si.due_date = add_days(today(), 30)
	si.company = co.company
	si.cost_center = co.cost_center
	si.project = co.project
	si.catering_order = co.name
	si.currency = co.currency
	si.conversion_rate = flt(co.conversion_rate) or 1
	si.selling_price_list = co.price_list

	_default_income = co.income_account or _settings_account("default_cogs_account")
	for it in co.items:
		si.append("items", {
			"item_code": it.item_code,
			"item_name": it.item_name,
			"qty": flt(it.total_qty) or 1,
			"rate": flt(it.rate),
			"uom": it.uom,
			"income_account": _default_income,
			"cost_center": co.cost_center,
		})

	if co.tax_template:
		si.taxes_and_charges = co.tax_template
		_apply_tax_template(si, co.tax_template, "Sales Taxes and Charges Template")

	if co.sales_order:
		for item in si.items:
			item.sales_order = co.sales_order

	# Apply additional discount from popup
	if additional_discount and flt(additional_discount) > 0:
		si.apply_discount_on = "Grand Total"
		si.discount_amount = flt(additional_discount)

	si.flags.ignore_permissions = True
	si.insert()

	if cint(auto_submit):
		try:
			si.submit()
		except Exception as e:
			frappe.msgprint(_("Sales Invoice {0} created but auto-submit failed: {1}").format(
				si.name, str(e)[:200]), indicator="orange")

	frappe.db.set_value("Catering Order", catering_order,
		{"sales_invoice": si.name, "status": "Invoiced"})
	_log_activity(co, "Document Created", f"Sales Invoice {si.name} created",
		"Sales Invoice", si.name)
	frappe.msgprint(_("Sales Invoice {0} {1}").format(
		get_link_to_form("Sales Invoice", si.name),
		"created and submitted" if cint(auto_submit) else "created"
	), indicator="green", alert=True)
	return si.name


@frappe.whitelist()
def get_payment_defaults(catering_order):
	"""Return defaults for the Payment Entry popup."""
	co = frappe.get_doc("Catering Order", catering_order)
	if not co.sales_invoice:
		return {"error": "Create the Sales Invoice first. Payments must reconcile against an Invoice."}

	si_data = frappe.db.get_value("Sales Invoice", co.sales_invoice,
		["grand_total", "outstanding_amount", "currency", "debit_to", "docstatus"], as_dict=True) or {}

	if si_data.get("docstatus") != 1:
		return {"error": "Sales Invoice must be submitted before recording payments."}

	if flt(si_data.get("outstanding_amount", 0)) <= 0:
		return {"error": "Sales Invoice is already fully paid."}

	settings = _get_settings()
	mode_of_payment = (settings.default_mode_of_payment if settings else None) or \
		frappe.db.get_value("Mode of Payment", {"enabled": 1}, "name")
	paid_to = co.advance_account or \
		(settings.default_bank_account if settings else None) or \
		(settings.default_advance_account if settings else None) or \
		frappe.db.get_value("Company", co.company, "default_bank_account") or \
		frappe.db.get_value("Company", co.company, "default_cash_account")

	return {
		"sales_invoice": co.sales_invoice,
		"invoice_grand_total": flt(si_data.get("grand_total", 0)),
		"invoice_outstanding": flt(si_data.get("outstanding_amount", 0)),
		"currency": si_data.get("currency", co.currency),
		"suggested_amount": flt(si_data.get("outstanding_amount", 0)),
		"mode_of_payment": mode_of_payment,
		"paid_to": paid_to,
		"reference_no_default": f"PAY-{co.name}",
		"reference_date_default": today(),
	}


@frappe.whitelist()
def create_payment_entry(catering_order, paid_amount=None, mode_of_payment=None,
						 paid_to=None, reference_no=None, reference_date=None,
						 auto_submit=0):
	"""Create Payment Entry that reconciles against the Sales Invoice."""
	co = frappe.get_doc("Catering Order", catering_order)
	_check_approval_gate(co)
	if not co.sales_invoice:
		frappe.throw(_("Create the Sales Invoice first. Payments must reconcile against an Invoice."))

	si = frappe.get_doc("Sales Invoice", co.sales_invoice)
	if si.docstatus != 1:
		frappe.throw(_("Sales Invoice must be submitted before recording payments."))
	if flt(si.outstanding_amount) <= 0:
		frappe.throw(_("Sales Invoice {0} is already fully paid.").format(co.sales_invoice))

	settings = _get_settings()

	amount = flt(paid_amount) if paid_amount else flt(si.outstanding_amount)
	if amount <= 0:
		frappe.throw(_("Payment amount must be greater than zero."))
	if amount > flt(si.outstanding_amount):
		frappe.throw(_("Payment amount {0} exceeds outstanding {1}.").format(
			amount, si.outstanding_amount))

	if not mode_of_payment:
		mode_of_payment = (settings.default_mode_of_payment if settings else None) or \
			frappe.db.get_value("Mode of Payment", {"enabled": 1}, "name") or \
			frappe.db.get_value("Mode of Payment", {}, "name")
	if not mode_of_payment:
		frappe.throw(_("No Mode of Payment configured. Set Default Mode of Payment in Catering Settings."))

	if not paid_to:
		if co.advance_account:
			paid_to = co.advance_account
		elif settings and settings.default_bank_account:
			paid_to = settings.default_bank_account
		elif settings and settings.default_advance_account:
			paid_to = settings.default_advance_account
		else:
			try:
				mop_doc = frappe.get_doc("Mode of Payment", mode_of_payment)
				for row in (mop_doc.accounts or []):
					if row.company == co.company:
						paid_to = row.default_account
						break
			except Exception:
				pass
	if not paid_to:
		paid_to = frappe.db.get_value("Company", co.company, "default_bank_account") or \
			frappe.db.get_value("Company", co.company, "default_cash_account")
	if not paid_to:
		frappe.throw(_("Could not determine Paid To account. Set 'Default Bank/Cash Account' in Catering Settings."))

	paid_from = si.debit_to or \
		(settings.default_receivable_account if settings else None) or \
		frappe.db.get_value("Company", co.company, "default_receivable_account")

	company_currency = frappe.db.get_value("Company", co.company, "default_currency") or "USD"
	order_currency = co.currency or company_currency

	pe = _create_doc_with_naming("Payment Entry")
	pe.payment_type = "Receive"
	pe.party_type = "Customer"
	pe.party = co.customer
	pe.posting_date = reference_date or today()
	pe.company = co.company
	pe.cost_center = co.cost_center
	pe.project = co.project
	pe.catering_order = co.name
	pe.mode_of_payment = mode_of_payment
	pe.paid_to = paid_to
	if paid_from:
		pe.paid_from = paid_from
	pe.paid_from_account_currency = order_currency
	pe.paid_to_account_currency = order_currency
	pe.source_exchange_rate = 1.0
	pe.target_exchange_rate = 1.0
	pe.paid_amount = amount
	pe.received_amount = amount
	pe.base_paid_amount = amount
	pe.base_received_amount = amount
	pe.reference_no = reference_no or f"PAY-{co.name}"
	pe.reference_date = reference_date or today()

	pe.append("references", {
		"reference_doctype": "Sales Invoice",
		"reference_name": co.sales_invoice,
		"total_amount": flt(si.grand_total),
		"outstanding_amount": flt(si.outstanding_amount),
		"allocated_amount": amount,
	})

	pe.flags.ignore_permissions = True
	pe.insert()

	if cint(auto_submit):
		try:
			pe.submit()
		except Exception as e:
			frappe.msgprint(_("Payment Entry {0} created but auto-submit failed: {1}").format(
				pe.name, str(e)[:200]), indicator="orange")
			return pe.name

	_log_activity(co, "Document Created", f"Payment Entry {pe.name} created", "Payment Entry", pe.name)
	frappe.msgprint(_("Payment Entry {0} {1}").format(
		get_link_to_form("Payment Entry", pe.name),
		"created and submitted" if cint(auto_submit) else "created — review and submit"
	), indicator="green", alert=True)
	return pe.name


def _get_total_paid(catering_order):
	"""Get total received from all submitted Payment Entries for this catering order."""
	co = frappe.get_doc("Catering Order", catering_order)
	total = 0.0

	# Direct payments (catering_order field)
	direct = _safe_sum_with_catering_link(
		"tabPayment Entry", "paid_amount", catering_order,
		"AND payment_type = 'Receive'"
	)
	total += flt(direct)

	# Payments via Sales Invoice references (avoid double-count by excluding ones already counted)
	if co.sales_invoice:
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
			""", (co.sales_invoice, catering_order))
			if via_invoice and via_invoice[0]:
				total += flt(via_invoice[0][0])
		except Exception:
			pass

	return total


@frappe.whitelist()
def create_cost_sheet(catering_order):
	"""Create Cost Sheet — auto-aggregates costs from ERPNext on save."""
	co = frappe.get_doc("Catering Order", catering_order)
	_check_approval_gate(co)
	existing = frappe.db.get_value("Catering Cost Sheet",
		{"catering_order": catering_order, "docstatus": ["!=", 2]}, "name")
	if existing:
		frappe.throw(_("Cost Sheet {0} already exists.").format(existing))

	cs = _create_doc_with_naming("Catering Cost Sheet")
	cs.catering_order = catering_order
	cs.cost_sheet_date = today()
	cs.company = co.company
	cs.branch = co.branch
	cs.currency = co.currency

	cs.flags.ignore_permissions = True
	cs.insert()
	frappe.db.set_value("Catering Order", catering_order, "cost_sheet", cs.name)
	_log_activity(co, "Document Created", f"Cost Sheet {cs.name} created", "Catering Cost Sheet", cs.name)
	frappe.msgprint(_("Cost Sheet {0} created. Costs will auto-populate on save.").format(
		get_link_to_form("Catering Cost Sheet", cs.name)), indicator="green", alert=True)
	return cs.name


@frappe.whitelist()
def create_production_plan(catering_order):
	"""Create Production Plan. Requires invoice + minimum payment %."""
	co = frappe.get_doc("Catering Order", catering_order)
	_check_approval_gate(co)
	settings = _get_settings()

	if not co.sales_invoice:
		frappe.throw(_("Create a Sales Invoice before starting Production. The invoice anchors all costs and payments."))

	if not cint(co.get("bypass_deposit")):
		total_paid = _get_total_paid(catering_order)
		invoice_total = flt(co.total_order_value)

		min_percent = 30.0
		if settings and flt(settings.get("minimum_payment_percent_for_production")):
			min_percent = flt(settings.minimum_payment_percent_for_production)

		required_min = invoice_total * min_percent / 100

		if total_paid < required_min:
			frappe.throw(_(
				"Minimum payment of {0}% ({1} {2}) required before Production. "
				"Currently received: {3} {2}. "
				"Collect more payment or have a Manager enable 'Bypass Deposit Requirement'."
			).format(
				min_percent,
				frappe.utils.fmt_money(required_min, currency=co.currency),
				co.currency,
				frappe.utils.fmt_money(total_paid, currency=co.currency)
			))

	existing = frappe.db.get_value("Catering Production Plan",
		{"catering_order": catering_order, "docstatus": ["!=", 2]}, "name")
	if existing:
		frappe.throw(_("Production Plan {0} already exists.").format(existing))

	pp = _create_doc_with_naming("Catering Production Plan")
	pp.catering_order = catering_order
	pp.company = co.company
	pp.planned_start_date = add_days(co.event_date, -2) if co.event_date else today()
	pp.planned_end_date = co.event_date

	# Production Plan inherits warehouses through Catering Settings at Work Order creation time
	# (no need to store on Production Plan itself)


	# Build a set of categories that require production (from Catering Item Category master)
	production_categories = set()
	try:
		rows = frappe.db.sql("""SELECT name FROM `tabCatering Item Category`
			WHERE requires_production = 1""", as_dict=True)
		production_categories = {r.name for r in rows}
	except Exception:
		pass

	# First pass: add only items that need production (manufactured OR category requires it)
	added_count = 0
	for it in co.items:
		if it.is_manufactured or (it.category and it.category in production_categories):
			pp.append("items", {
				"item_code": it.item_code,
				"item_name": it.item_name,
				"bom": it.bom,
				"planned_qty": flt(it.total_qty),
				"uom": it.uom,
				"status": "Pending",
			})
			added_count += 1

	# Fallback: if no items matched the filter, add ALL items (better than empty plan)
	if added_count == 0:
		for it in co.items:
			pp.append("items", {
				"item_code": it.item_code,
				"item_name": it.item_name,
				"bom": it.bom,
				"planned_qty": flt(it.total_qty),
				"uom": it.uom,
				"status": "Pending",
			})

	# If still empty, the order itself has no items
	if not pp.items:
		frappe.throw(_("Cannot create Production Plan: Catering Order has no items. Add items to the order first."))

	pp.flags.ignore_permissions = True
	pp.insert()
	frappe.db.set_value("Catering Order", catering_order,
		{"production_plan": pp.name, "status": "In Production"})
	_log_activity(co, "Document Created", f"Production Plan {pp.name} created",
		"Catering Production Plan", pp.name)
	frappe.msgprint(_("Production Plan {0} created").format(
		get_link_to_form("Catering Production Plan", pp.name)), indicator="green", alert=True)
	return pp.name


@frappe.whitelist()
def create_material_request(catering_order):
	"""Create Material Request - explodes BOMs to compute raw materials."""
	co = frappe.get_doc("Catering Order", catering_order)
	_check_approval_gate(co)
	if co.material_request:
		frappe.throw(_("Material Request {0} already exists.").format(co.material_request))

	required_items = _calculate_raw_materials(co)
	if not required_items:
		for it in co.items:
			if it.item_code:
				required_items[it.item_code] = {
					"qty": flt(it.total_qty),
					"uom": it.uom,
					"item_name": it.item_name,
				}

	if not required_items:
		frappe.throw(_("No items found to create Material Request. Add items to the Catering Order first."))

	mr = _create_doc_with_naming("Material Request")
	mr.material_request_type = "Purchase"
	mr.transaction_date = today()
	# schedule_date must be >= transaction_date (today). Use later of preferred date or today+1.
	preferred_schedule = add_days(co.event_date, -3) if co.event_date else add_days(today(), 3)
	min_schedule = today()  # cannot be before transaction date
	mr.schedule_date = max(getdate(preferred_schedule), getdate(min_schedule))
	mr.company = co.company
	mr.cost_center = co.cost_center
	mr.project = co.project
	mr.catering_order = co.name

	for item_code, info in required_items.items():
		mr.append("items", {
			"item_code": item_code,
			"item_name": info.get("item_name"),
			"qty": info["qty"],
			"uom": info.get("uom"),
			"schedule_date": mr.schedule_date,
			"cost_center": co.cost_center,
			"project": co.project,
		})

	mr.flags.ignore_permissions = True
	mr.insert()
	frappe.db.set_value("Catering Order", catering_order, "material_request", mr.name)
	_log_activity(co, "Document Created", f"Material Request {mr.name} created",
		"Material Request", mr.name)
	frappe.msgprint(_("Material Request {0} created").format(
		get_link_to_form("Material Request", mr.name)), indicator="green", alert=True)
	return mr.name


def _calculate_raw_materials(co):
	"""Explode BOMs to compute raw material requirements."""
	required = {}
	for it in co.items:
		if not it.bom:
			continue
		try:
			bom = frappe.get_cached_doc("BOM", it.bom)
			multiplier = flt(it.total_qty) / flt(bom.quantity or 1)
			for ri in bom.items:
				code = ri.item_code
				qty_needed = flt(ri.qty) * multiplier
				if code in required:
					required[code]["qty"] += qty_needed
				else:
					required[code] = {
						"qty": qty_needed,
						"uom": ri.uom or ri.stock_uom,
						"item_name": ri.item_name,
					}
		except frappe.DoesNotExistError:
			continue
	return required


@frappe.whitelist()
def create_delivery_plan(catering_order):
	"""Create Delivery Plan for the event day."""
	co = frappe.get_doc("Catering Order", catering_order)
	_check_approval_gate(co)
	if co.delivery_plan:
		frappe.throw(_("Delivery Plan {0} already exists.").format(co.delivery_plan))

	settings = _get_settings()

	dp = _create_doc_with_naming("Catering Delivery Plan")
	dp.catering_order = catering_order
	dp.customer = co.customer_name or co.customer
	dp.delivery_date = co.event_date or today()
	dp.delivery_time = co.service_start_time or "12:00:00"
	dp.delivery_address = co.event_address or co.event_location
	dp.contact_person = co.contact_person or ""
	dp.contact_phone = co.contact_mobile or ""
	dp.company = co.company
	dp.status = "Planned"

	for it in co.items:
		dp.append("delivery_items", {
			"item_code": it.item_code,
			"item_name": it.item_name,
			"qty": flt(it.total_qty),
			"uom": it.uom,
		})

	dp.flags.ignore_permissions = True
	dp.insert()
	frappe.db.set_value("Catering Order", catering_order, "delivery_plan", dp.name)
	_log_activity(co, "Document Created", f"Delivery Plan {dp.name} created",
		"Catering Delivery Plan", dp.name)
	frappe.msgprint(_("Delivery Plan {0} created").format(
		get_link_to_form("Catering Delivery Plan", dp.name)), indicator="green", alert=True)
	return dp.name


@frappe.whitelist()
def create_closing_sheet(catering_order):
	"""Create Closing Sheet — pulls all financial data from linked docs."""
	co = frappe.get_doc("Catering Order", catering_order)
	_check_approval_gate(co)
	existing = frappe.db.get_value("Catering Closing Sheet",
		{"catering_order": catering_order, "docstatus": ["!=", 2]}, "name")
	if existing:
		frappe.throw(_("Closing Sheet {0} already exists.").format(existing))

	settings = _get_settings()
	if settings and cint(settings.require_invoice_before_closure) and not co.sales_invoice:
		frappe.throw(_("A Sales Invoice is required before creating a Closing Sheet."))

	cs = _create_doc_with_naming("Catering Closing Sheet")
	cs.catering_order = catering_order
	cs.closing_date = today()
	cs.company = co.company
	cs.currency = co.currency

	cs.total_revenue = flt(co.total_order_value)
	cs.invoiced_amount = _safe_sum_with_catering_link("tabSales Invoice", "grand_total", catering_order)
	cs.payment_received = _get_total_paid(catering_order)
	cs.outstanding = cs.invoiced_amount - cs.payment_received

	if co.cost_sheet:
		try:
			csheet = frappe.db.get_value("Catering Cost Sheet", co.cost_sheet,
				["food_cost", "beverage_cost", "labor_cost", "delivery_cost"], as_dict=True) or {}
			cs.food_cost = csheet.get("food_cost", 0)
			cs.beverage_cost = csheet.get("beverage_cost", 0)
			cs.labor_cost = csheet.get("labor_cost", 0)
			cs.delivery_cost = csheet.get("delivery_cost", 0)
		except Exception:
			pass

	cs.total_wastage = _safe_sum_with_catering_link("tabCatering Wastage Entry", "total_wastage_value", catering_order)
	cs.total_emergency_expense = _safe_sum_with_catering_link("tabCatering Emergency Expense", "total_amount", catering_order)

	cs.total_cost = (flt(cs.food_cost) + flt(cs.beverage_cost) + flt(cs.labor_cost)
					 + flt(cs.delivery_cost) + flt(cs.total_wastage)
					 + flt(cs.total_emergency_expense))
	cs.gross_profit = flt(cs.total_revenue) - flt(cs.total_cost)
	cs.net_profit = cs.gross_profit
	cs.gross_margin_percent = (cs.gross_profit / flt(cs.total_revenue) * 100) if flt(cs.total_revenue) else 0

	cs.flags.ignore_permissions = True
	cs.insert()
	frappe.db.set_value("Catering Order", catering_order, "closing_sheet", cs.name)
	_log_activity(co, "Document Created", f"Closing Sheet {cs.name} created",
		"Catering Closing Sheet", cs.name)
	frappe.msgprint(_("Closing Sheet {0} created — review and approve before closing.").format(
		get_link_to_form("Catering Closing Sheet", cs.name)), indicator="green", alert=True)
	return cs.name


@frappe.whitelist()
def close_catering_order(catering_order):
	"""Close the Catering Order after Closing Sheet is approved."""
	co = frappe.get_doc("Catering Order", catering_order)
	if not co.closing_sheet:
		frappe.throw(_("Create a Closing Sheet first."))

	settings = _get_settings()
	if settings and cint(settings.require_profit_review_before_closure):
		cs_status = frappe.db.get_value("Catering Closing Sheet", co.closing_sheet, "status")
		if cs_status not in ("Reviewed", "Approved", "Closed"):
			frappe.throw(_(
				"Closing Sheet must be Reviewed or Approved before closing the order. Current status: {0}"
			).format(cs_status))

	frappe.db.set_value("Catering Order", catering_order, "status", "Closed")
	_log_activity(co, "Closing", f"Catering Order {catering_order} closed")
	frappe.msgprint(_("Catering Order {0} closed successfully.").format(catering_order),
		indicator="green", alert=True)
	return "Closed"


@frappe.whitelist()
def get_profitability(catering_order):
	"""Return profitability snapshot."""
	co = frappe.get_doc("Catering Order", catering_order)

	revenue = flt(co.total_order_value)
	invoiced = _safe_sum_with_catering_link("tabSales Invoice", "grand_total", catering_order)
	paid = _get_total_paid(catering_order)
	cost = _safe_sum_with_catering_link("tabCatering Cost Sheet", "total_cost", catering_order)
	wastage = _safe_sum_with_catering_link("tabCatering Wastage Entry", "total_wastage_value", catering_order)
	emergency = _safe_sum_with_catering_link("tabCatering Emergency Expense", "total_amount", catering_order)

	total_cost = cost + wastage + emergency
	profit = revenue - total_cost
	margin = (profit / revenue * 100) if revenue else 0

	return {
		"revenue": revenue, "invoiced": invoiced, "paid": paid,
		"outstanding": invoiced - paid, "cost": cost, "wastage": wastage,
		"emergency": emergency, "total_cost": total_cost,
		"gross_profit": profit, "gross_margin_percent": round(margin, 2),
		"currency": co.currency,
	}


# ════════════════════════════════════════════════════════════════════════════
# HELPERS (continued)
# ════════════════════════════════════════════════════════════════════════════

def _apply_tax_template(target_doc, template_name, template_doctype):
	"""Copy tax rows from a template to target doc."""
	try:
		template = frappe.get_doc(template_doctype, template_name)
		for tax in template.taxes:
			target_doc.append("taxes", {
				"charge_type": tax.charge_type,
				"account_head": tax.account_head,
				"description": tax.description,
				"rate": tax.rate,
				"cost_center": tax.cost_center,
			})
	except Exception:
		pass


@frappe.whitelist()
def update_sales_invoice(catering_order):
	"""Sync the Sales Invoice with current Catering Order contents.

	Behavior depends on Sales Invoice docstatus:
	- DRAFT (0): edit the SI items directly to match the current order.
	- SUBMITTED (1): create a SUPPLEMENTARY Sales Invoice for the DIFFERENCE only.
	  → If items were added or quantities increased → new charge invoice for the extra.
	  → If items were removed or quantities decreased → a credit note (return) for the reduction.

	This approach preserves Sales Order, Work Orders, payments, and audit trail. Nothing is cancelled.
	"""
	co = frappe.get_doc("Catering Order", catering_order)
	if not co.sales_invoice:
		frappe.throw(_("This order has no Sales Invoice yet. Create one first."))

	si = frappe.get_doc("Sales Invoice", co.sales_invoice)

	# ── Case A: SI is still DRAFT — edit in place ─────────────────────────────
	if si.docstatus == 0:
		si.set("items", [])
		for it in co.items:
			si.append("items", {
				"item_code": it.item_code,
				"item_name": it.item_name,
				"qty": flt(it.total_qty) or 1,
				"rate": flt(it.rate),
				"uom": it.uom,
				"income_account": co.income_account or _settings_account("default_cogs_account"),
				"cost_center": co.cost_center,
			})
		si.flags.ignore_permissions = True
		si.save()
		co.last_billed_snapshot = _compute_order_snapshot(co)
		co.requires_rebill = 0
		co.db_update()
		_log_activity(co, "Document Submitted", f"Sales Invoice {si.name} updated in place (draft)",
			"Sales Invoice", si.name)
		frappe.msgprint(_("Sales Invoice {0} updated in-place with current order contents.").format(
			get_link_to_form("Sales Invoice", si.name)), indicator="green", alert=True)
		return {"action": "updated_draft", "sales_invoice": si.name}

	# ── Case B: SI is SUBMITTED — compute the DIFFERENCE and post a supplementary doc ──
	# Build a map of currently-billed quantities (sum across all SI lines per item_code)
	billed_map = {}  # item_code -> {qty, rate}
	for sii in si.items:
		code = sii.item_code
		if code in billed_map:
			billed_map[code]["qty"] += flt(sii.qty)
		else:
			billed_map[code] = {"qty": flt(sii.qty), "rate": flt(sii.rate),
			                    "item_name": sii.item_name, "uom": sii.uom}

	# Also include supplementary invoices that already exist for this order
	prior_supps = frappe.db.sql("""
		SELECT sii.item_code, sii.qty
		FROM `tabSales Invoice Item` sii
		INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.catering_order = %s
		  AND si.name != %s
		  AND si.docstatus = 1
	""", (catering_order, si.name), as_dict=True)
	for row in prior_supps:
		code = row.item_code
		if code in billed_map:
			billed_map[code]["qty"] += flt(row.qty)

	# Build current expected quantities
	current_map = {}
	for it in co.items:
		code = it.item_code
		if code in current_map:
			current_map[code]["qty"] += flt(it.total_qty)
		else:
			current_map[code] = {
				"qty": flt(it.total_qty),
				"rate": flt(it.rate),
				"item_name": it.item_name,
				"uom": it.uom,
			}

	# Compute the DIFFERENCE per item
	diffs = []  # list of {item_code, qty_diff (signed), rate, item_name, uom}
	all_codes = set(billed_map.keys()) | set(current_map.keys())
	for code in all_codes:
		billed_qty = flt(billed_map.get(code, {}).get("qty", 0))
		current_qty = flt(current_map.get(code, {}).get("qty", 0))
		diff = current_qty - billed_qty
		if abs(diff) < 0.001:
			continue
		rate = current_map.get(code, {}).get("rate") or billed_map.get(code, {}).get("rate", 0)
		item_name = current_map.get(code, {}).get("item_name") or billed_map.get(code, {}).get("item_name")
		uom = current_map.get(code, {}).get("uom") or billed_map.get(code, {}).get("uom")
		diffs.append({"item_code": code, "qty_diff": diff, "rate": rate,
		              "item_name": item_name, "uom": uom})

	if not diffs:
		# Nothing has changed quantity-wise — just refresh snapshot
		co.last_billed_snapshot = _compute_order_snapshot(co)
		co.requires_rebill = 0
		co.db_update()
		frappe.msgprint(_("No billing changes detected. Order is already in sync with the Invoice."),
			indicator="blue", alert=True)
		return {"action": "no_change", "sales_invoice": si.name}

	# Split into positive (additional charge) and negative (credit note) lists
	positive_diffs = [d for d in diffs if d["qty_diff"] > 0]
	negative_diffs = [d for d in diffs if d["qty_diff"] < 0]

	new_si_names = []

	# (a) Create supplementary invoice for additions
	if positive_diffs:
		sup = _create_doc_with_naming("Sales Invoice")
		sup.customer = co.customer
		sup.posting_date = today()
		sup.due_date = add_days(today(), 30)
		sup.company = co.company
		sup.cost_center = co.cost_center
		sup.project = co.project
		sup.catering_order = co.name
		sup.currency = co.currency
		sup.conversion_rate = flt(co.conversion_rate) or 1
		sup.selling_price_list = co.price_list
		sup.remarks = f"Supplementary invoice (additions) for Catering Order {co.name}"

		for d in positive_diffs:
			sup.append("items", {
				"item_code": d["item_code"],
				"item_name": d["item_name"],
				"qty": d["qty_diff"],
				"rate": d["rate"],
				"uom": d["uom"],
				"income_account": co.income_account or _settings_account("default_cogs_account"),
				"cost_center": co.cost_center,
			})

		if co.tax_template:
			sup.taxes_and_charges = co.tax_template
			_apply_tax_template(sup, co.tax_template, "Sales Taxes and Charges Template")

		sup.flags.ignore_permissions = True
		sup.insert()
		try:
			sup.submit()
		except Exception as e:
			frappe.msgprint(_("Supplementary SI {0} created (Draft). Submit failed: {1}").format(
				sup.name, str(e)[:200]), indicator="orange")
		new_si_names.append(("addition", sup.name))

	# (b) Create credit note (Return SI) for removals
	if negative_diffs:
		cn = _create_doc_with_naming("Sales Invoice")
		cn.is_return = 1
		cn.return_against = si.name
		cn.customer = co.customer
		cn.posting_date = today()
		cn.company = co.company
		cn.cost_center = co.cost_center
		cn.project = co.project
		cn.catering_order = co.name
		cn.currency = co.currency
		cn.conversion_rate = flt(co.conversion_rate) or 1
		cn.selling_price_list = co.price_list
		cn.remarks = f"Credit note (reductions) for Catering Order {co.name}"

		for d in negative_diffs:
			# Return SI uses NEGATIVE qty
			cn.append("items", {
				"item_code": d["item_code"],
				"item_name": d["item_name"],
				"qty": d["qty_diff"],  # already negative
				"rate": d["rate"],
				"uom": d["uom"],
				"income_account": co.income_account or _settings_account("default_cogs_account"),
				"cost_center": co.cost_center,
			})

		cn.flags.ignore_permissions = True
		cn.insert()
		try:
			cn.submit()
		except Exception as e:
			frappe.msgprint(_("Credit Note {0} created (Draft). Submit failed: {1}").format(
				cn.name, str(e)[:200]), indicator="orange")
		new_si_names.append(("credit_note", cn.name))

	# Update snapshot
	co.last_billed_snapshot = _compute_order_snapshot(co)
	co.requires_rebill = 0
	co.db_update()

	for kind, name in new_si_names:
		_log_activity(co, "Document Created",
			f"{'Supplementary SI' if kind == 'addition' else 'Credit Note'} {name} for order changes",
			"Sales Invoice", name)

	msg_parts = []
	for kind, name in new_si_names:
		label = "Supplementary Invoice" if kind == "addition" else "Credit Note"
		msg_parts.append(f"{label}: {get_link_to_form('Sales Invoice', name)}")
	frappe.msgprint(_("Created: {0}").format(" | ".join(msg_parts)), indicator="green", alert=True)

	return {"action": "supplementary", "documents": [n for _, n in new_si_names]}


def _settings_account(key):
	"""Fetch an account fieldname from Catering Settings safely."""
	try:
		return frappe.db.get_single_value("Catering Settings", key)
	except Exception:
		return None


@frappe.whitelist()
def create_delivery_note_from_plan(delivery_plan):
	"""Create a Delivery Note from a Catering Delivery Plan.

	Behavior:
	- If a Sales Invoice exists for the order, the DN references it via against_sales_invoice
	  (each DN item links to the corresponding SI item so ERPNext knows it's already billed).
	- For items already fully billed/delivered, skip them.
	- For items not yet delivered or partial, only deliver the remaining qty.
	- DN status is auto-set by ERPNext based on Sales Invoice status.
	"""
	dp = frappe.get_doc("Catering Delivery Plan", delivery_plan)
	co = frappe.get_doc("Catering Order", dp.catering_order) if dp.catering_order else None

	settings = _get_settings()

	dn = _create_doc_with_naming("Delivery Note")
	dn.customer = co.customer if co else None
	dn.posting_date = today()
	dn.posting_time = dp.delivery_time or "12:00:00"
	dn.company = dp.company or (co.company if co else None)
	dn.cost_center = co.cost_center if co else None
	dn.project = co.project if co else None
	dn.catering_order = dp.catering_order

	dn.currency = (co.currency if co else None) or "USD"
	dn.conversion_rate = 1.0

	if dp.delivery_address:
		dn.shipping_address = dp.delivery_address
	if dp.contact_phone:
		dn.contact_mobile = dp.contact_phone

	from_wh = (settings.default_fg_warehouse if settings else None)
	if not from_wh:
		from_wh = frappe.db.get_value("Item Default", {"parent": dp.delivery_items[0].item_code}, "default_warehouse") \
			if dp.delivery_items else None

	# Load the Sales Invoice items if available — we link DN items to SI items so the DN
	# does NOT re-bill what's already invoiced. ERPNext handles this via against_sales_invoice + si_detail.
	si_items_by_code = {}
	si_name = None
	if co and co.sales_invoice:
		try:
			si = frappe.get_doc("Sales Invoice", co.sales_invoice)
			if si.docstatus == 1:
				si_name = si.name
				for sii in si.items:
					# Track remaining-to-deliver: invoiced qty - already-delivered qty
					already_delivered = flt(sii.delivered_qty or 0)
					remaining = flt(sii.qty) - already_delivered
					si_items_by_code[sii.item_code] = {
						"si_item_name": sii.name,
						"si_qty": flt(sii.qty),
						"delivered_qty": already_delivered,
						"remaining": remaining,
						"rate": flt(sii.rate),
					}
		except Exception:
			pass

	# Build DN items — skip items already fully delivered/billed
	items_added = 0
	items_skipped = []
	for item in (dp.delivery_items or []):
		item_code = item.item_code
		qty_to_deliver = flt(item.qty)

		# Match against SI line if exists
		if item_code in si_items_by_code:
			si_info = si_items_by_code[item_code]
			if si_info["remaining"] <= 0:
				# Already fully delivered/billed
				items_skipped.append(f"{item_code} (already delivered)")
				continue
			# Cap to remaining qty
			qty_to_deliver = min(qty_to_deliver, si_info["remaining"])

			dn.append("items", {
				"item_code": item_code,
				"item_name": item.item_name,
				"qty": qty_to_deliver,
				"uom": item.uom,
				"warehouse": from_wh,
				"cost_center": co.cost_center if co else None,
				"rate": si_info["rate"],
				"against_sales_invoice": si_name,
				"si_detail": si_info["si_item_name"],
				"sales_invoice_item": si_info["si_item_name"],
			})
			items_added += 1
		else:
			# Item not in Sales Invoice — deliver as-is (will be billed later or marked unbilled)
			dn.append("items", {
				"item_code": item_code,
				"item_name": item.item_name,
				"qty": qty_to_deliver,
				"uom": item.uom,
				"warehouse": from_wh,
				"cost_center": co.cost_center if co else None,
			})
			items_added += 1

	if items_added == 0:
		msg = "All items already delivered/billed."
		if items_skipped:
			msg += " Skipped: " + ", ".join(items_skipped[:5])
		frappe.throw(_(msg))

	dn.flags.ignore_permissions = True
	dn.insert()

	# Link back from Delivery Plan and update its status
	dp_status = "Partially Delivered" if items_skipped else "Delivered"
	frappe.db.set_value("Catering Delivery Plan", delivery_plan, {
		"delivery_note": dn.name,
		"status": dp_status,
	}, update_modified=False)

	if co:
		note = f"Delivery Note {dn.name} created from Delivery Plan {delivery_plan}"
		if items_skipped:
			note += f" (skipped {len(items_skipped)} already-billed items)"
		_log_activity(co, "Document Created", note, "Delivery Note", dn.name)

	return dn.name


@frappe.whitelist()
def create_work_orders_from_plan(production_plan):
	"""Create one Work Order per Production Plan item.

	WIP and FG warehouses are inherited from Catering Settings (NOT manually).
	Each Work Order's catering_order field is set for cost-sheet attribution.
	"""
	pp = frappe.get_doc("Catering Production Plan", production_plan)
	if pp.docstatus != 1:
		frappe.throw(_("Production Plan must be submitted before creating Work Orders."))

	settings = _get_settings()
	if not settings:
		frappe.throw(_("Catering Settings not configured. Cannot determine warehouses."))

	# Production Plan doctype has no warehouse fields - use Catering Settings directly
	wip = settings.default_wip_warehouse
	fg = settings.default_fg_warehouse
	source = settings.default_source_warehouse

	if not wip or not fg:
		frappe.throw(_("WIP and FG warehouses must be configured in Catering Settings."))

	created = []
	for item in (pp.items or []):
		if item.work_order:
			continue  # Already has one
		if not item.bom:
			continue  # Can't create WO without BOM

		try:
			wo = frappe.new_doc("Work Order")
			wo.production_item = item.item_code
			wo.bom_no = item.bom
			wo.qty = flt(item.planned_qty)
			wo.company = pp.company
			wo.wip_warehouse = wip
			wo.fg_warehouse = fg
			wo.source_warehouse = source
			wo.planned_start_date = pp.planned_start_date or today()
			wo.expected_delivery_date = pp.planned_end_date or today()
			wo.catering_order = pp.catering_order
			wo.use_multi_level_bom = 0

			wo.flags.ignore_permissions = True
			wo.insert()

			# Link back to Production Plan Item
			frappe.db.set_value("Catering Production Plan Item", item.name, "work_order", wo.name)
			created.append(wo.name)
		except Exception as e:
			frappe.log_error(f"Work Order creation failed for {item.item_code}: {str(e)[:200]}",
				"Production Plan Work Order")

	frappe.db.commit()
	return created


# ════════════════════════════════════════════════════════════════════════════
# APPROVAL WORKFLOW — permission-based
# ════════════════════════════════════════════════════════════════════════════

MANAGER_ROLES = ("Catering Manager", "Catering Management", "System Manager", "Administrator")


def _is_manager():
	return any(r in frappe.get_roles() for r in MANAGER_ROLES)


def _check_approval_gate(co):
	"""Raise if order is not Approved. Called by every create_* method."""
	approval = co.get("approval_status") or "Draft"
	if approval != "Approved":
		frappe.throw(_(
			"Cannot proceed: this Catering Order is in '{0}' state. "
			"It must be Approved by a Catering Manager before any documents can be created."
		).format(approval), title=_("Approval Required"))


@frappe.whitelist()
def submit_for_approval(catering_order):
	"""Move from Draft → Pending Approval. Anyone with write permission can call."""
	co = frappe.get_doc("Catering Order", catering_order)
	if co.approval_status not in ("Draft", "Rejected"):
		frappe.throw(_("Can only submit for approval from Draft or Rejected state. Current: {0}").format(
			co.approval_status))

	# Basic completeness check
	if not co.items:
		frappe.throw(_("Add items to the order before submitting for approval."))
	if not co.customer or not co.event_date or not co.total_guests:
		frappe.throw(_("Customer, Event Date, and Total Guests are required before approval."))

	frappe.db.set_value("Catering Order", catering_order, {
		"approval_status": "Pending Approval",
		"status": "Pending Approval",
	}, update_modified=True)

	_log_activity(co, "Approval", f"Submitted for approval by {frappe.session.user}")
	frappe.msgprint(_("Order submitted for approval. Awaiting Catering Manager review."),
		indicator="orange", alert=True)
	return "Pending Approval"


@frappe.whitelist()
def approve_order(catering_order):
	"""Move to Approved. Only Catering Manager."""
	if not _is_manager():
		frappe.throw(_("Only Catering Manager can approve orders."),
			frappe.PermissionError)
	co = frappe.get_doc("Catering Order", catering_order)
	if co.approval_status not in ("Pending Approval", "Draft"):
		frappe.throw(_("Can only approve orders in Pending Approval or Draft state. Current: {0}").format(
			co.approval_status))

	frappe.db.set_value("Catering Order", catering_order, {
		"approval_status": "Approved",
		"status": "Approved",
	}, update_modified=True)

	_log_activity(co, "Approval", f"Approved by {frappe.session.user}")
	frappe.msgprint(_("Order approved. You can now proceed to create Sales Order."),
		indicator="green", alert=True)
	return "Approved"


@frappe.whitelist()
def reject_order(catering_order, reason=None):
	"""Reject. Only Catering Manager. Can be undone by Reopen."""
	if not _is_manager():
		frappe.throw(_("Only Catering Manager can reject orders."), frappe.PermissionError)
	co = frappe.get_doc("Catering Order", catering_order)
	if co.approval_status in ("Approved",):
		frappe.throw(_("Cannot reject an Approved order. Use Cancel instead."))

	frappe.db.set_value("Catering Order", catering_order, {
		"approval_status": "Rejected",
		"status": "Cancelled",
	}, update_modified=True)

	_log_activity(co, "Rejection",
		f"Rejected by {frappe.session.user}" + (f" — Reason: {reason}" if reason else ""))
	frappe.msgprint(_("Order rejected. Sales user can revise and resubmit, or Manager can Reopen."),
		indicator="red", alert=True)
	return "Rejected"


@frappe.whitelist()
def reopen_order(catering_order):
	"""Undo a Rejection — move back to Draft. Manager only."""
	if not _is_manager():
		frappe.throw(_("Only Catering Manager can reopen rejected orders."),
			frappe.PermissionError)
	co = frappe.get_doc("Catering Order", catering_order)
	if co.approval_status != "Rejected":
		frappe.throw(_("Can only reopen Rejected orders. Current: {0}").format(co.approval_status))

	frappe.db.set_value("Catering Order", catering_order, {
		"approval_status": "Draft",
		"status": "Draft",
	}, update_modified=True)

	_log_activity(co, "Approval", f"Rejection undone by {frappe.session.user}")
	frappe.msgprint(_("Order reopened. Back to Draft state."),
		indicator="blue", alert=True)
	return "Draft"

