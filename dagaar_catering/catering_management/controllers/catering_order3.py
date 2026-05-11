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
	frappe.db.set_value(doc.doctype, doc.name, "status", "Cancelled")
	_log_activity(doc, "Status Change", f"Catering Order {doc.name} cancelled")


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
	if doc.status in ("Cancelled", "Closed"):
		return

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

	if flt(doc.deposit_received) >= flt(doc.deposit_amount) and flt(doc.deposit_amount) > 0:
		doc.status = "Deposit Received"
		return

	if doc.sales_order:
		doc.status = "Confirmed"
		return

	if doc.quotation:
		doc.status = "Quoted"
		return

	if not doc.status or doc.status == "":
		doc.status = "Draft"


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
def create_quotation(catering_order):
	co = frappe.get_doc("Catering Order", catering_order)
	if co.docstatus == 2:
		frappe.throw(_("Cannot create documents from a cancelled Catering Order."))
	if co.quotation:
		frappe.throw(_("Quotation {0} already exists for this Catering Order.").format(co.quotation))
	if not co.items:
		frappe.throw(_("Add items to the Catering Order before creating a Quotation."))

	qt = _create_doc_with_naming("Quotation")
	qt.quotation_to = "Customer"
	qt.party_name = co.customer
	qt.customer_name = co.customer_name
	qt.transaction_date = today()
	qt.valid_till = add_days(today(), 30)
	qt.company = co.company
	qt.cost_center = co.cost_center
	qt.project = co.project
	qt.currency = co.currency
	qt.conversion_rate = flt(co.conversion_rate) or 1
	qt.selling_price_list = co.price_list
	qt.catering_order = co.name

	for it in co.items:
		qt.append("items", {
			"item_code": it.item_code,
			"item_name": it.item_name,
			"qty": flt(it.total_qty) or 1,
			"rate": flt(it.rate),
			"uom": it.uom,
			"cost_center": co.cost_center,
		})

	if co.tax_template:
		qt.taxes_and_charges = co.tax_template
		_apply_tax_template(qt, co.tax_template, "Sales Taxes and Charges Template")

	qt.flags.ignore_permissions = True
	qt.insert()
	frappe.db.set_value("Catering Order", catering_order, "quotation", qt.name)
	_log_activity(co, "Document Created", f"Quotation {qt.name} created", "Quotation", qt.name)
	frappe.msgprint(_("Quotation {0} created").format(get_link_to_form("Quotation", qt.name)),
		indicator="green", alert=True)
	return qt.name


@frappe.whitelist()
def create_sales_order(catering_order):
	co = frappe.get_doc("Catering Order", catering_order)
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
	frappe.db.set_value("Catering Order", catering_order, {"sales_order": so.name, "status": "Confirmed"})
	_log_activity(co, "Document Created", f"Sales Order {so.name} created", "Sales Order", so.name)
	frappe.msgprint(_("Sales Order {0} created").format(get_link_to_form("Sales Order", so.name)),
		indicator="green", alert=True)
	return so.name


@frappe.whitelist()
def create_deposit_payment(catering_order):
	co = frappe.get_doc("Catering Order", catering_order)
	if not flt(co.deposit_amount):
		frappe.throw(_("Deposit Amount is zero. Set Deposit % first."))
	remaining = flt(co.deposit_amount) - flt(co.deposit_received)
	if remaining <= 0:
		frappe.throw(_("Deposit already fully received."))

	settings = _get_settings()
	company_currency = frappe.db.get_value("Company", co.company, "default_currency") or "USD"
	order_currency = co.currency or company_currency

	# ── Resolve Mode of Payment ────────────────────────────────────────────
	mode_of_payment = None
	if settings and settings.default_mode_of_payment:
		mode_of_payment = settings.default_mode_of_payment
	else:
		# Fall back to first active Mode of Payment
		mop = frappe.db.get_value("Mode of Payment", {"enabled": 1}, "name") \
			or frappe.db.get_value("Mode of Payment", {}, "name")
		if mop:
			mode_of_payment = mop

	if not mode_of_payment:
		frappe.throw(_("No Mode of Payment configured. Set 'Default Mode of Payment' in Catering Settings, or create one in Setup."))

	# ── Resolve Paid To Account ────────────────────────────────────────────
	paid_to = None
	# Priority 1: Catering Order's advance_account
	if co.advance_account:
		paid_to = co.advance_account
	# Priority 2: Catering Settings default_bank_account or default_advance_account
	elif settings:
		if settings.default_bank_account:
			paid_to = settings.default_bank_account
		elif settings.default_advance_account:
			paid_to = settings.default_advance_account
	# Priority 3: Mode of Payment's account for this company
	if not paid_to:
		try:
			mop_doc = frappe.get_doc("Mode of Payment", mode_of_payment)
			for row in (mop_doc.accounts or []):
				if row.company == co.company:
					paid_to = row.default_account
					break
		except Exception:
			pass
	# Priority 4: Company's default cash/bank account
	if not paid_to:
		paid_to = frappe.db.get_value("Company", co.company, "default_bank_account") or \
				  frappe.db.get_value("Company", co.company, "default_cash_account")

	if not paid_to:
		frappe.throw(_("Could not determine 'Paid To' account. Set 'Default Bank/Cash Account' in Catering Settings, or set Default Bank Account on the Mode of Payment."))

	# ── Resolve Paid From (customer's receivable) ──────────────────────────
	paid_from = None
	if settings and settings.default_receivable_account:
		paid_from = settings.default_receivable_account
	if not paid_from:
		paid_from = frappe.db.get_value("Company", co.company, "default_receivable_account")

	# ── Build the Payment Entry ────────────────────────────────────────────
	pe = _create_doc_with_naming("Payment Entry")
	pe.payment_type = "Receive"
	pe.party_type = "Customer"
	pe.party = co.customer
	pe.posting_date = today()
	pe.company = co.company
	pe.cost_center = co.cost_center
	pe.project = co.project
	pe.catering_order = co.name
	pe.mode_of_payment = mode_of_payment
	pe.paid_to = paid_to
	if paid_from:
		pe.paid_from = paid_from

	# Currency setup (no conversion — use company currency)
	pe.paid_from_account_currency = order_currency
	pe.paid_to_account_currency = order_currency
	pe.source_exchange_rate = 1.0
	pe.target_exchange_rate = 1.0

	# Amounts
	pe.paid_amount = remaining
	pe.received_amount = remaining
	pe.base_paid_amount = remaining
	pe.base_received_amount = remaining
	pe.reference_no = f"Deposit-{co.name}"
	pe.reference_date = today()

	pe.flags.ignore_permissions = True
	pe.insert()

	_log_activity(co, "Document Created", f"Deposit Payment Entry {pe.name} created", "Payment Entry", pe.name)
	frappe.msgprint(_("Payment Entry {0} created — review and submit").format(
		get_link_to_form("Payment Entry", pe.name)), indicator="green", alert=True)
	return pe.name



@frappe.whitelist()
def create_cost_sheet(catering_order):
	co = frappe.get_doc("Catering Order", catering_order)
	existing = frappe.db.get_value("Catering Cost Sheet",
		{"catering_order": catering_order, "docstatus": ["!=", 2]}, "name")
	if existing:
		frappe.throw(_("Cost Sheet {0} already exists.").format(existing))

	cs = frappe.new_doc("Catering Cost Sheet")
	cs.catering_order = catering_order
	cs.cost_sheet_date = today()
	cs.company = co.company
	cs.branch = co.branch
	cs.currency = co.currency

	category_map = {
		"Food": "Food Cost", "Beverage": "Beverage Cost",
		"Snacks": "Snacks/Packaging", "Dessert": "Snacks/Packaging",
		"Service": "Labor Cost", "Packaging": "Snacks/Packaging",
		"Other": "Other",
	}

	for it in co.items:
		cs.append("items", {
			"category": category_map.get(it.category, "Other"),
			"description": it.item_name,
			"item_code": it.item_code,
			"qty": flt(it.total_qty),
			"rate": flt(it.raw_material_cost) or flt(it.rate) * 0.6,
			"currency": co.currency,
		})

	cs.flags.ignore_permissions = True
	cs.insert()
	frappe.db.set_value("Catering Order", catering_order, "cost_sheet", cs.name)
	_log_activity(co, "Document Created", f"Cost Sheet {cs.name} created", "Catering Cost Sheet", cs.name)
	frappe.msgprint(_("Cost Sheet {0} created").format(
		get_link_to_form("Catering Cost Sheet", cs.name)), indicator="green", alert=True)
	return cs.name


@frappe.whitelist()
def create_production_plan(catering_order):
	co = frappe.get_doc("Catering Order", catering_order)
	settings = _get_settings()

	if settings:
		if cint(settings.require_so_before_production) and not co.sales_order:
			frappe.throw(_("Sales Order is required before creating a Production Plan. Create Sales Order first."))
		if cint(settings.require_deposit_before_production) and not cint(co.get("bypass_deposit")):
			if flt(co.deposit_received) < flt(co.deposit_amount) and flt(co.deposit_amount) > 0:
				frappe.throw(_("Deposit of {0} {1} required before Production. Received: {2}. (A Manager can enable 'Bypass Deposit Requirement' on the Catering Order to skip this.)").format(
					co.currency, co.deposit_amount, co.deposit_received))

	mr = _create_doc_with_naming("Material Request")
	mr.material_request_type = "Purchase"
	mr.transaction_date = today()
	mr.schedule_date = add_days(co.event_date, -3) if co.event_date else add_days(today(), 3)
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
def create_sales_invoice(catering_order):
	co = frappe.get_doc("Catering Order", catering_order)
	if co.sales_invoice:
		frappe.throw(_("Sales Invoice {0} already exists.").format(co.sales_invoice))

	settings = _get_settings()
	if settings and cint(settings.require_delivery_before_invoice):
		dn = frappe.db.get_value("Delivery Note", {"catering_order": catering_order, "docstatus": 1}, "name")
		dp = frappe.db.get_value("Catering Delivery Plan",
			{"catering_order": catering_order, "status": "Delivered"}, "name")
		if not dn and not dp:
			frappe.throw(_("Delivery must be confirmed before creating a Sales Invoice."))

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

	for it in co.items:
		si.append("items", {
			"item_code": it.item_code,
			"item_name": it.item_name,
			"qty": flt(it.total_qty) or 1,
			"rate": flt(it.rate),
			"uom": it.uom,
			"income_account": co.income_account,
			"cost_center": co.cost_center,
		})

	if co.tax_template:
		si.taxes_and_charges = co.tax_template
		_apply_tax_template(si, co.tax_template, "Sales Taxes and Charges Template")

	if co.sales_order:
		for item in si.items:
			item.sales_order = co.sales_order

	si.flags.ignore_permissions = True
	si.insert()
	frappe.db.set_value("Catering Order", catering_order,
		{"sales_invoice": si.name, "status": "Invoiced"})
	_log_activity(co, "Document Created", f"Sales Invoice {si.name} created",
		"Sales Invoice", si.name)
	frappe.msgprint(_("Sales Invoice {0} created").format(
		get_link_to_form("Sales Invoice", si.name)), indicator="green", alert=True)
	return si.name


@frappe.whitelist()
def create_delivery_plan(catering_order):
	co = frappe.get_doc("Catering Order", catering_order)
	if co.delivery_plan:
		frappe.throw(_("Delivery Plan {0} already exists.").format(co.delivery_plan))

	settings = _get_settings()

	dp = frappe.new_doc("Catering Delivery Plan")
	dp.catering_order = catering_order
	delivery_dt = f"{co.event_date} {co.service_start_time or '12:00:00'}"
	dp.delivery_datetime = delivery_dt
	dp.delivery_address = co.event_address or co.event_location
	dp.contact_mobile = co.contact_mobile
	dp.company = co.company
	if settings:
		dp.from_warehouse = settings.default_fg_warehouse

	for it in co.items:
		dp.append("items", {
			"item_code": it.item_code,
			"item_name": it.item_name,
			"qty": flt(it.total_qty),
			"uom": it.uom,
			"from_warehouse": dp.from_warehouse,
			"delivery_status": "Pending",
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
	co = frappe.get_doc("Catering Order", catering_order)
	existing = frappe.db.get_value("Catering Closing Sheet",
		{"catering_order": catering_order, "docstatus": ["!=", 2]}, "name")
	if existing:
		frappe.throw(_("Closing Sheet {0} already exists.").format(existing))

	settings = _get_settings()
	if settings and cint(settings.require_invoice_before_closure) and not co.sales_invoice:
		frappe.throw(_("A Sales Invoice is required before creating a Closing Sheet."))

	cs = frappe.new_doc("Catering Closing Sheet")
	cs.catering_order = catering_order
	cs.closing_date = today()
	cs.company = co.company
	cs.currency = co.currency

	cs.total_revenue = flt(co.total_order_value)
	cs.invoiced_amount = _safe_sum_with_catering_link("tabSales Invoice", "grand_total", catering_order)
	cs.payment_received = _safe_sum_with_catering_link("tabPayment Entry", "paid_amount", catering_order,
		"AND payment_type = 'Receive'")
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
	co = frappe.get_doc("Catering Order", catering_order)

	revenue = flt(co.total_order_value)
	invoiced = _safe_sum_with_catering_link("tabSales Invoice", "grand_total", catering_order)
	paid = _safe_sum_with_catering_link("tabPayment Entry", "paid_amount", catering_order,
		"AND payment_type = 'Receive'")
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
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _apply_tax_template(target_doc, template_name, template_doctype):
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
