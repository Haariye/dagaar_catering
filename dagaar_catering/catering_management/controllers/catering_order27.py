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
	"""MD5 hash of the order's billable contents.

	Includes:
	  - menu_packages rows (menu_package + guest_count)
	  - items rows (item_code + total_qty + rate)
	  - total_guests
	  - discount_amount / discount_percent

	If this hash changes between saves and a Sales Invoice exists,
	requires_rebill is set so on_update will auto-sync the SI.
	"""
	import hashlib, json
	payload = {
		"total_guests": flt(doc.total_guests),
		"discount_amount": flt(doc.discount_amount or 0),
		"discount_percent": flt(doc.discount_percent or 0),
		"menu_packages": sorted([
			{"pkg": p.menu_package, "gc": flt(p.guest_count), "label": p.guest_label or ""}
			for p in (doc.get("menu_packages") or [])
		], key=lambda x: (str(x["pkg"]), x["gc"])),
		"items": sorted([
			{
				"code": it.item_code,
				"qty": flt(it.total_qty),
				"rate": flt(it.rate),
			}
			for it in (doc.get("items") or []) if it.item_code
		], key=lambda x: (str(x["code"]), x["qty"], x["rate"])),
	}
	j = json.dumps(payload, sort_keys=True, default=str)
	return hashlib.md5(j.encode()).hexdigest()



def _check_for_rebilling_required(doc):
	"""Set requires_rebill=1 when the user has EDITED the order after invoicing.

	Compares a snapshot of (items + packages + totals) to last_billed_snapshot.
	If they differ → user changed something → flag for Regenerate Bill.

	This function ONLY compares. The snapshot is set ONLY when an SI is
	created or regenerated (in create_sales_invoice / update_sales_invoice).
	"""
	if not doc.get("sales_invoice"):
		doc.requires_rebill = 0
		return

	current = _compute_order_snapshot(doc)
	last = doc.get("last_billed_snapshot") or ""

	if not last:
		# Backfill once for legacy orders
		doc.last_billed_snapshot = current
		doc.requires_rebill = 0
		return

	doc.requires_rebill = 1 if current != last else 0



def _get_package_sales_invoice(catering_order):
	"""Return the package (non-additional-service) Sales Invoice for this order.

	The "package SI" is the one that bills the catering order's main items
	(packages + items child tables). Additional service SIs (setup fees,
	late hours, etc.) are tracked separately and excluded.

	Returns the SI name (oldest first if multiple exist), or None.
	"""
	try:
		row = frappe.db.sql("""
			SELECT name FROM `tabSales Invoice`
			WHERE catering_order = %s
			  AND docstatus IN (0, 1)
			  AND IFNULL(is_additional_service, 0) = 0
			  AND IFNULL(is_return, 0) = 0
			ORDER BY creation ASC
			LIMIT 1
		""", catering_order)
		return row[0][0] if row else None
	except Exception:
		return None


def _get_net_billed_amount(catering_order):
	"""Net PACKAGE billed amount = sum of (qty × rate) across all submitted
	Sales Invoice Items for this order, EXCLUDING any item that:
	  - Belongs to its SI which has is_additional_service=1, OR
	  - Belongs to item_group='Service' (cross-check, in case the flag wasn't set)

	This computes at the LINE level so a mixed credit note (packages + services
	cancelled together) is handled correctly — only the package portion is subtracted.

	Credit note line qty is already negative, so SUM(qty × rate) gives the net.
	"""
	try:
		row = frappe.db.sql("""
			SELECT IFNULL(SUM(sii.qty * sii.rate), 0) AS net_billed
			FROM `tabSales Invoice Item` sii
			INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
			LEFT JOIN `tabItem` i ON i.name = sii.item_code
			WHERE si.catering_order = %s
			  AND si.docstatus = 1
			  AND IFNULL(si.is_additional_service, 0) = 0
			  AND IFNULL(i.item_group, '') != 'Service'
		""", catering_order)
		return flt(row[0][0]) if row else 0
	except Exception:
		return 0



@frappe.whitelist()
def _heal_sales_invoice_field(catering_order):
	"""Self-healing: if co.sales_invoice happens to point to a service SI
	(from the old buggy hook), clear it. The real package SI is found via
	_get_package_sales_invoice anyway.
	"""
	try:
		current = frappe.db.get_value("Catering Order", catering_order, "sales_invoice")
		if not current:
			return
		is_service = frappe.db.get_value("Sales Invoice", current,
			"is_additional_service") or 0
		if is_service:
			# This field shouldn't point to a service SI. Find the real package SI.
			pkg = _get_package_sales_invoice(catering_order)
			frappe.db.set_value("Catering Order", catering_order,
				"sales_invoice", pkg, update_modified=False)
	except Exception:
		pass


@frappe.whitelist()
def get_billing_status(catering_order):
	"""Return billing snapshot for the UI — drives SI + Regenerate Bill visibility.

	Returns dict:
	  order_total: total_order_value (current after amendments)
	  net_billed: SUM(SI) - SUM(credit notes), submitted only
	  unbilled_amount: order_total - net_billed
	  show_sales_invoice_btn: bool  (unbilled > 0.01 means more to bill)
	  show_regenerate_btn: bool     (an SI exists AND amounts differ)
	  currency
	"""
	_heal_sales_invoice_field(catering_order)
	order = frappe.db.get_value("Catering Order", catering_order,
		["total_order_value", "currency", "sales_order"],
		as_dict=True) or {}
	order_total = flt(order.get("total_order_value"))
	net_billed = _get_net_billed_amount(catering_order)
	unbilled = order_total - net_billed

	# Use the actual package SI (not the stored field) — additional service SIs
	# do not count as "having a Sales Invoice" for the package billing flow.
	package_si = _get_package_sales_invoice(catering_order)

	return {
		"order_total": order_total,
		"net_billed": net_billed,
		"unbilled_amount": unbilled,
		"has_sales_order": bool(order.get("sales_order")),
		"has_sales_invoice": bool(package_si),
		"package_sales_invoice": package_si,
		"show_sales_invoice_btn": bool(order.get("sales_order")) and not package_si and unbilled > 0.01,
		"show_regenerate_btn": bool(package_si) and abs(unbilled) > 0.01,
		"currency": order.get("currency"),
	}



def validate(doc, method=None):
	"""Catering Order validate hook."""
	_check_locked_after_production(doc)
	_validate_guests(doc)
	_calculate_totals(doc)
	_set_status(doc)
	_check_for_rebilling_required(doc)


def _check_locked_after_production(doc):
	"""Once a Production Plan is created/linked, certain fields are frozen.

	Blocks edits to: menu_packages, items, total_guests, discount.
	The rule: if any production has started, changes must go to a NEW Catering Order.

	Bypass: managers can override (rare) by setting flags.ignore_pp_lock.
	"""
	if not doc.production_plan:
		return  # No production plan yet — everything editable
	if doc.docstatus != 1:
		return  # Not submitted yet — no lock
	if getattr(doc.flags, "ignore_pp_lock", False):
		return  # Manager override

	# Compare current state to last-persisted state
	prev = doc.get_doc_before_save()
	if not prev:
		return  # First save — nothing to compare

	locked_fields = ["menu_packages", "items", "total_guests"]
	changed = []

	for field in locked_fields:
		new_val = doc.get(field)
		old_val = prev.get(field)
		if field in ("menu_packages", "items"):
			# Compare child tables by content fingerprint
			if _table_fingerprint(new_val) != _table_fingerprint(old_val):
				changed.append(field)
		elif flt(new_val) != flt(old_val):
			changed.append(field)

	if changed:
		frappe.throw(_(
			"This Catering Order has reached the Production Plan stage. "
			"The following fields are locked and cannot be changed: <b>{0}</b>. "
			"To make changes, please create a new Catering Order for the customer."
		).format(", ".join(changed)),
		title=_("Order Locked — Production Started"))


def _table_fingerprint(rows):
	"""Stable hash of a child-table contents (ignoring row order/idx)."""
	if not rows:
		return ""
	import hashlib, json
	data = []
	for r in rows:
		if hasattr(r, 'as_dict'):
			d = r.as_dict()
		else:
			d = dict(r)
		# Exclude unstable fields
		for k in ('name', 'idx', 'modified', 'creation', 'owner', 'modified_by',
		          'docstatus', 'parent', 'parenttype', 'parentfield'):
			d.pop(k, None)
		data.append(d)
	# Sort by item_code if present
	try:
		data.sort(key=lambda x: str(x.get('item_code') or x.get('menu_package') or ''))
	except Exception:
		pass
	j = json.dumps(data, default=str, sort_keys=True)
	return hashlib.md5(j.encode()).hexdigest()



def before_save(doc, method=None):
	if not doc.get("created_by_name"):
		doc.created_by_name = frappe.utils.get_fullname(frappe.session.user)
	if doc.customer and not doc.contact_email:
		_fetch_primary_contact(doc)


def after_insert(doc, method=None):
	_log_activity(doc, "Order Created", f"Catering Order created for {doc.customer_name or doc.customer}")
	# Auto-load items if menu_packages were already added during creation
	if doc.get("menu_packages") and not doc.items:
		_load_menu_packages_items(doc)
		doc.save(ignore_permissions=True)


def on_update(doc, method=None):
	"""No-op.

	Amendment workflow is purely manual now:
	  1. User edits menu_packages or items
	  2. User clicks Save
	  3. validate() detects the change via snapshot, sets requires_rebill=1
	  4. The red Regenerate Bill button appears
	  5. User clicks Regenerate Bill -> update_sales_invoice runs explicitly
	"""
	pass



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



# ════════════════════════════════════════════════════════════════════════════
# CALCULATIONS
# ════════════════════════════════════════════════════════════════════════════

def _calculate_totals(doc):
	"""Recompute order totals from menu_packages and items child tables.

	New model: menu_packages is a child table where each row has a package and a guest count.
	  total_guests = SUM of all guest_count values across packages
	  Items in the `items` child table carry their own guest_count (set during load).
	  total_qty per item = qty_per_guest × that item's guest_count
	  amount per item = total_qty × rate

	The `subtotal` is the sum of item amounts plus tax/discount.
	"""
	# Total guests = sum across packages
	total_guests = 0
	for pkg_row in (doc.get("menu_packages") or []):
		total_guests += int(pkg_row.guest_count or 0)
		# Compute subtotal per package row for display
		try:
			pkg_price = flt(frappe.db.get_value("Catering Menu Package", pkg_row.menu_package, "price_per_guest")) or 0
			pkg_row.subtotal = pkg_price * flt(pkg_row.guest_count or 0)
		except Exception:
			pkg_row.subtotal = 0

	doc.total_guests = total_guests

	# Recompute item totals.
	# Rule: if qty_per_guest AND guest_count are both set, derive total_qty.
	# Otherwise (e.g. user added a one-off item row directly), respect the
	# total_qty already on the row — don't zero it out.
	subtotal = 0
	for item in (doc.items or []):
		qpg = flt(item.qty_per_guest or 0)
		gc = flt(item.guest_count or 0)
		if qpg > 0 and gc > 0:
			item.total_qty = qpg * gc
		elif not flt(item.total_qty):
			# Last resort: if user gave qty_per_guest only, multiply by order's total_guests
			if qpg > 0 and total_guests > 0:
				item.total_qty = qpg * total_guests
				item.guest_count = total_guests
		item.amount = flt(item.total_qty) * flt(item.rate or 0)
		subtotal += flt(item.amount)

	doc.subtotal = subtotal

	# Discount
	if flt(doc.discount_percent):
		doc.discount_amount = subtotal * flt(doc.discount_percent) / 100
	elif flt(doc.discount_amount):
		doc.discount_percent = (flt(doc.discount_amount) / subtotal * 100) if subtotal else 0

	# Total order value
	doc.total_order_value = (
		flt(doc.subtotal)
		- flt(doc.discount_amount or 0)
		+ flt(doc.total_taxes or 0)
	)

	# Deposit suggestion
	if flt(doc.deposit_percent):
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
	"""Validate guest counts:
	  - Each package row must have guest_count >= 1
	  - Each package row's guest_count must be >= package.minimum_guests (if set)
	  - Total guests across all packages must be > 0

	No max enforcement — packages can scale up freely.
	"""
	if not (doc.get("menu_packages") or []):
		frappe.throw(_("At least one menu package row is required."),
			title=_("Menu Packages Missing"))

	# Check for duplicate menu_package values across rows
	seen = {}
	for idx, p in enumerate(doc.get("menu_packages") or [], start=1):
		if not p.menu_package:
			continue
		if p.menu_package in seen:
			frappe.throw(
				_("Menu Package <b>{0}</b> appears in rows #{1} and #{2}. "
				  "Combine the guest counts into a single row instead.").format(
					p.menu_package, seen[p.menu_package], idx),
				title=_("Duplicate Package"))
		seen[p.menu_package] = idx

	errors = []

	# Per-row zero check + min_guests check
	for idx, pkg_row in enumerate(doc.get("menu_packages") or [], start=1):
		if not pkg_row.menu_package:
			errors.append(_("Row #{0}: Menu Package is empty").format(idx))
			continue

		gc = flt(pkg_row.guest_count or 0)
		if gc < 1:
			errors.append(_("Row #{0} ({1}): guest count must be at least 1").format(
				idx, pkg_row.menu_package))
			continue

		try:
			pkg = frappe.get_cached_doc("Catering Menu Package", pkg_row.menu_package)
		except Exception:
			continue  # package master missing — let other validation handle it

		min_g = flt(pkg.minimum_guests or 0)
		if min_g > 0 and gc < min_g:
			errors.append(
				_("Row #{0} <b>{1}</b>: guest count <b>{2}</b> is below minimum <b>{3}</b>").format(
					idx, pkg.package_name or pkg_row.menu_package, int(gc), int(min_g)
				)
			)

	if errors:
		frappe.throw(
			"<br>".join(errors) + "<br><br>" +
			_("Fix the rows above before saving."),
			title=_("Guest Count Validation Failed")
		)

	# Final total check (defensive)
	if flt(doc.total_guests) <= 0:
		frappe.throw(_("Total Guests must be greater than 0."),
			title=_("Guest Count Validation Failed"))



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
	"""Auto-set status based on docstatus and linked-doc state.

	Status precedence:
	  1. Cancelled / Closed (terminal — never change)
	  2. docstatus 2 → Cancelled
	  3. docstatus 0 → Draft
	  4. docstatus 1 (Submitted): check operational state
	     - sales_invoice exists & outstanding <= 0 → Paid
	     - sales_invoice exists → Invoiced
	     - delivery_note exists → Delivered
	     - delivery_plan delivered → Delivered
	     - production_plan complete → Ready to Deliver
	     - production_plan exists → In Production
	     - sales_order exists → Confirmed
	     - else → Confirmed (submitted, ready to start)
	"""
	if doc.status in ("Cancelled", "Closed"):
		return

	if doc.docstatus == 2:
		doc.status = "Cancelled"
		return

	if doc.docstatus == 0:
		doc.status = "Draft"
		return

	# docstatus == 1 — submitted, compute operational status
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

	# Submitted but no operational documents yet
	doc.status = "Confirmed"



def _set_title(doc):
	parts = [doc.event_type or "Event", doc.customer_name or doc.customer or "", str(doc.event_date or "")]
	doc.title = " - ".join(p for p in parts if p)


# ════════════════════════════════════════════════════════════════════════════
# ACTIVITY LOGGING
# ════════════════════════════════════════════════════════════════════════════

def _log_activity(doc, activity_type, description, ref_dt=None, ref_name=None):
	"""Log significant activities only. Returns silently for routine events.

	Allowed types (the meaningful ones):
	  - Order Created
	  - Order Submitted
	  - Order Cancelled
	  - Order Voided
	  - Order Closed
	  - Order Reopened
	  - Rejection (kept for audit)

	For everything else (Status Change, Document Created, Document Submitted,
	Document Cancelled, Payment Received, Production Update, Delivery Update,
	Note Added, Approval, Other), Frappe's built-in Version + Comment + Activity
	history already provides full audit — no need for custom logging.
	"""
	ALLOWED = {
		"Order Created", "Order Submitted", "Order Cancelled",
		"Order Voided", "Order Closed", "Order Reopened", "Rejection",
	}
	if activity_type not in ALLOWED:
		return  # silently skip noise

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




def _load_menu_packages_items(doc):
	"""Load items into the order's items table by exploding all menu_packages × guest_count.

	For each menu package row (with its own guest count), iterate package's items and add
	rows to the order's items table, each with qty_per_guest × guest_count = total_qty.
	Duplicates (same item_code) are NOT merged — kept separate per package so the user
	can see which package each row came from.
	"""
	if not doc.get("menu_packages"):
		return

	# Clear existing items if any (only when explicitly reloading)
	doc.set("items", [])

	for pkg_row in doc.menu_packages:
		try:
			pkg = frappe.get_cached_doc("Catering Menu Package", pkg_row.menu_package)
		except Exception:
			continue

		for pi in (pkg.items or []):
			doc.append("items", {
				"item_code": pi.item_code,
				"item_name": pi.item_name,
				"category": pi.category,
				"qty_per_guest": flt(pi.qty_per_guest or 0),
				"uom": pi.uom,
				"rate": flt(pi.rate or 0),
				"bom": pi.bom,
				"is_manufactured": pi.is_manufactured,
				"wastage_percent": flt(pi.wastage_percent or 0),
				"guest_count": int(pkg_row.guest_count or 0),
				"menu_package_item": pi.name,
				"currency": doc.currency,
			})

	_calculate_totals(doc)


@frappe.whitelist()
def load_menu_packages_items(catering_order):
	"""Whitelisted endpoint to reload items from packages.

	Works for both Draft (docstatus=0) and Submitted (docstatus=1) orders.
	For submitted orders, we use the special update_after_submit path that
	respects allow_on_submit=1 on the items child table.
	"""
	co = frappe.get_doc("Catering Order", catering_order)
	if co.docstatus == 2:
		frappe.throw(_("Cannot reload items on a cancelled order."))
	if co.status == "Closed":
		frappe.throw(_("Cannot reload items on a Closed order."))

	_load_menu_packages_items(co)

	if co.docstatus == 0:
		# Draft — normal save path
		co.save(ignore_permissions=True)
	else:
		# Submitted — use update_after_submit which respects allow_on_submit
		co.flags.ignore_permissions = True
		co.flags.ignore_validate_update_after_submit = False
		try:
			co.save(ignore_permissions=True)
		except frappe.exceptions.UpdateAfterSubmitError:
			# Fall back to direct DB manipulation if Frappe complains about disallowed fields
			_force_persist_after_submit(co)

	return co.name


def _force_persist_after_submit(co):
	"""Persist changes to a submitted Catering Order using db_set + child SQL.

	Used when normal save() raises UpdateAfterSubmitError on fields that don't
	have allow_on_submit=1 (we should rarely hit this, but it's a safety net).
	"""
	# Clear existing items in the DB
	frappe.db.delete("Catering Order Item", {"parent": co.name})

	# Re-insert the items
	for idx, item in enumerate(co.get("items") or []):
		row = frappe.new_doc("Catering Order Item")
		row.parent = co.name
		row.parenttype = "Catering Order"
		row.parentfield = "items"
		row.idx = idx + 1
		row.item_code = item.item_code
		row.item_name = item.item_name
		row.category = item.category
		row.qty_per_guest = item.qty_per_guest
		row.uom = item.uom
		row.rate = item.rate
		row.bom = item.bom
		row.is_manufactured = item.is_manufactured
		row.wastage_percent = item.wastage_percent
		row.guest_count = item.guest_count
		row.menu_package_item = item.menu_package_item
		row.total_qty = item.total_qty
		row.amount = item.amount
		row.currency = co.currency
		row.db_insert()

	# Update the parent totals via db_set (works for allow_on_submit fields)
	for field in ('total_guests', 'subtotal', 'discount_amount', 'total_order_value',
	              'deposit_amount', 'balance_due'):
		try:
			frappe.db.set_value("Catering Order", co.name, field,
				co.get(field) or 0, update_modified=False)
		except Exception:
			pass

	co.flags.requires_rebill = 1
	frappe.db.set_value("Catering Order", co.name, "requires_rebill", 1, update_modified=False)
	frappe.db.commit()


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
	existing_pkg_si = _get_package_sales_invoice(catering_order)
	if existing_pkg_si:
		return {"error": f"Package Sales Invoice {existing_pkg_si} already exists for this order."}
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
	# Block ONLY if a non-additional-service Sales Invoice already exists.
	# Additional service SIs (setup fees, late hours) don't block package billing.
	existing_pkg_si = _get_package_sales_invoice(catering_order)
	if existing_pkg_si:
		frappe.throw(_("Sales Invoice {0} already exists for this order.").format(existing_pkg_si))

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
	# Capture the snapshot of what we just billed.
	# Use frappe.db.set_value (not co.db_update) — co is stale at this point
	# and db_update would overwrite sales_invoice with None.
	snapshot = _compute_order_snapshot(co)
	frappe.db.set_value("Catering Order", catering_order, {
		"last_billed_snapshot": snapshot,
		"requires_rebill": 0,
	}, update_modified=False)

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
def get_unpaid_invoices(catering_order):
	"""All submitted, non-return Sales Invoices for this order with
	outstanding > 0. Ordered oldest first (FIFO).

	Drives the Payment Entry dialog's invoice picker. Includes package SIs,
	supplementary SIs, AND additional service SIs.
	"""
	rows = frappe.db.sql("""
		SELECT name, posting_date, grand_total, outstanding_amount, currency,
		       is_return, debit_to
		FROM `tabSales Invoice`
		WHERE catering_order = %s
		  AND docstatus = 1
		  AND outstanding_amount > 0.01
		  AND IFNULL(is_return, 0) = 0
		ORDER BY posting_date ASC, creation ASC
	""", catering_order, as_dict=True)
	return rows


@frappe.whitelist()
def create_payment_entry(catering_order, allocations=None, mode_of_payment=None,
                          paid_to=None, reference_no=None, reference_date=None,
                          auto_submit=0, paid_amount=None):
	"""Create Payment Entry that reconciles against ONE OR MORE Sales Invoices.

	`allocations` JSON: [{"invoice": "ACC-SINV-...", "amount": 50.0}, ...]
	Each amount is capped to the invoice's outstanding. Falls back to legacy
	co.sales_invoice + paid_amount if allocations is not provided.

	This is the multi-invoice flow that lets the user pick which unpaid SIs
	to pay (package, supplementary, additional service).
	"""
	import json as _json

	co = frappe.get_doc("Catering Order", catering_order)
	_check_approval_gate(co)

	# Parse allocations
	if isinstance(allocations, str):
		try:
			allocations = _json.loads(allocations)
		except Exception:
			allocations = None

	if not allocations:
		# Legacy fallback — single SI
		if not co.sales_invoice:
			frappe.throw(_("Create the Sales Invoice first."))
		allocations = [{
			"invoice": co.sales_invoice,
			"amount": flt(paid_amount) if paid_amount else None,
		}]

	if not allocations:
		frappe.throw(_("No invoices selected for payment."))

	settings = _get_settings()
	total_amount = 0
	si_refs = []
	currency = None
	debit_to = None

	for alloc in allocations:
		si_name = alloc.get("invoice")
		if not si_name:
			continue
		si = frappe.get_doc("Sales Invoice", si_name)
		if si.docstatus != 1:
			frappe.throw(_("Sales Invoice {0} not submitted.").format(si_name))
		if flt(si.outstanding_amount) <= 0:
			frappe.throw(_("Sales Invoice {0} already fully paid.").format(si_name))

		amount = flt(alloc.get("amount"))
		if amount <= 0:
			amount = flt(si.outstanding_amount)
		if amount > flt(si.outstanding_amount):
			frappe.throw(_("Amount {0} for {1} exceeds outstanding {2}.").format(
				amount, si_name, si.outstanding_amount))

		currency = currency or si.currency
		debit_to = debit_to or si.debit_to
		total_amount += amount
		si_refs.append({"si": si, "amount": amount})

	if total_amount <= 0:
		frappe.throw(_("Total payment must be greater than zero."))

	if not mode_of_payment:
		mode_of_payment = (settings.default_mode_of_payment if settings else None) or \
			frappe.db.get_value("Mode of Payment", {"enabled": 1}, "name") or \
			frappe.db.get_value("Mode of Payment", {}, "name")

	if not paid_to:
		paid_to = co.advance_account or \
			(settings.default_bank_account if settings else None) or \
			(settings.default_advance_account if settings else None) or \
			frappe.db.get_value("Company", co.company, "default_bank_account") or \
			frappe.db.get_value("Company", co.company, "default_cash_account")
	if not paid_to:
		frappe.throw(_("Could not resolve a Paid To account."))

	pe = _create_doc_with_naming("Payment Entry")
	pe.payment_type = "Receive"
	pe.party_type = "Customer"
	pe.party = co.customer
	pe.company = co.company
	pe.posting_date = today()
	pe.mode_of_payment = mode_of_payment
	pe.paid_from = debit_to
	pe.paid_to = paid_to
	pe.paid_from_account_currency = currency
	pe.paid_to_account_currency = frappe.db.get_value("Account", paid_to,
		"account_currency") or currency
	pe.received_amount = total_amount
	pe.paid_amount = total_amount
	pe.source_exchange_rate = 1
	pe.target_exchange_rate = 1
	pe.project = co.project
	pe.cost_center = co.cost_center
	pe.catering_order = catering_order
	pe.reference_no = reference_no or f"PMT-{co.name}"
	pe.reference_date = reference_date or today()

	for ref in si_refs:
		pe.append("references", {
			"reference_doctype": "Sales Invoice",
			"reference_name": ref["si"].name,
			"total_amount": flt(ref["si"].grand_total),
			"outstanding_amount": flt(ref["si"].outstanding_amount),
			"allocated_amount": ref["amount"],
		})

	pe.flags.ignore_permissions = True
	pe.insert()

	if cint(auto_submit):
		try:
			pe.submit()
		except Exception as e:
			frappe.msgprint(_("PE {0} created (Draft). Submit failed: {1}").format(
				pe.name, str(e)[:200]), indicator="orange")

	_log_activity(co, "Payment Received",
		f"PE {pe.name}: {total_amount} {currency} across {len(si_refs)} invoice(s)",
		"Payment Entry", pe.name)
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
	"""Live P&L for a Catering Order — queries source data directly. No cache.

	Returns a dict with:
	  - revenue, invoiced, paid, outstanding (sales-side numbers)
	  - food_cost, beverage_cost, snacks_cost, labor_cost, delivery_cost,
	    rental_cost, overhead_cost (cost categories)
	  - wastage, emergency (informational only — already in overhead_cost)
	  - total_cost, gross_profit, gross_margin_percent
	  - currency

	This is the ONE source of truth for P&L. All UI cards, the script report,
	and the Closing Sheet read from this function.
	"""
	return _compute_catering_pnl(catering_order)



def _wo_operating_cost_column():
	"""Return whichever operating-cost column exists on tabWork Order.

	ERPNext 15 uses `total_operating_cost`. Older versions had `operating_cost`.
	Returns None if neither column exists.
	"""
	try:
		rows = frappe.db.sql("""
			SELECT column_name FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = 'tabWork Order'
			  AND column_name IN ('total_operating_cost', 'operating_cost')
		""")
		if not rows:
			return None
		names = {r[0] for r in rows}
		if 'total_operating_cost' in names:
			return 'total_operating_cost'
		if 'operating_cost' in names:
			return 'operating_cost'
		return None
	except Exception:
		return None

def _compute_catering_pnl(catering_order):
	"""Live P&L computation — pure read, no writes anywhere."""
	co = frappe.db.get_value("Catering Order", catering_order,
		["name", "total_order_value", "currency", "company"], as_dict=True)
	if not co:
		return {}

	company = co.company
	currency = co.currency

	# ─── Revenue side ────────────────────────────────────────────────────
	# Total Sales Invoices (net of credit notes)
	invoiced_row = frappe.db.sql("""
		SELECT IFNULL(SUM(
			CASE WHEN is_return = 1 THEN -ABS(grand_total) ELSE grand_total END
		), 0) AS amt
		FROM `tabSales Invoice`
		WHERE catering_order = %s AND docstatus = 1
	""", catering_order, as_dict=True)
	invoiced = flt(invoiced_row[0].amt) if invoiced_row else 0

	revenue = invoiced if invoiced else flt(co.total_order_value)

	paid = _get_total_paid(catering_order)
	outstanding = max(0, invoiced - paid)

	# ─── Cost side — bucket map from Settings ────────────────────────────
	settings = None
	try:
		settings = frappe.get_single("Catering Settings")
	except Exception:
		pass

	food_acct = settings.get("default_food_cogs_account") if settings else None
	labor_acct = settings.get("default_labor_cost_account") if settings else None
	delivery_acct = settings.get("default_delivery_cost_account") if settings else None

	# Initialise cost buckets
	food_cost = 0
	beverage_cost = 0
	snacks_cost = 0
	labor_cost = 0
	delivery_cost = 0
	rental_cost = 0
	overhead_cost = 0

	# ─── Stock consumption from tagged Stock Entries ─────────────────────
	# Bucketed by category via the Catering Order Item table
	food_cost     += _stock_consumption(catering_order, ["Food", "Dessert"])
	beverage_cost += _stock_consumption(catering_order, ["Beverage"])
	snacks_cost   += _stock_consumption(catering_order, ["Snacks"])

	# ─── Work Order labor cost → labor bucket ────────────────────────────
	# ERPNext 15 uses `total_operating_cost`; older versions had `operating_cost`.
	# Detect at runtime so we don't crash either way.
	wo_col = _wo_operating_cost_column()
	if wo_col:
		wo_op_row = frappe.db.sql(f"""
			SELECT IFNULL(SUM({wo_col}), 0) AS amt
			FROM `tabWork Order`
			WHERE catering_order = %s AND docstatus = 1
		""", catering_order, as_dict=True)
		labor_cost += flt(wo_op_row[0].amt) if wo_op_row else 0

	# ─── Journal Entry expense debits — bucketed by account ──────────────
	je_rows = _safe_table_query("tabJournal Entry", """
		SELECT jea.account,
		       IFNULL(SUM(jea.debit_in_account_currency), 0) AS amt
		FROM `tabJournal Entry` je
		INNER JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
		INNER JOIN `tabAccount` acc ON acc.name = jea.account
		WHERE je.docstatus = 1
		  AND je.catering_order = %s
		  AND acc.root_type = 'Expense'
		  AND jea.debit_in_account_currency > 0
		GROUP BY jea.account
	""", catering_order)

	for r in (je_rows or []):
		amt = flt(r.amt)
		if food_acct and r.account == food_acct:
			food_cost += amt
		elif labor_acct and r.account == labor_acct:
			labor_cost += amt
		elif delivery_acct and r.account == delivery_acct:
			delivery_cost += amt
		else:
			overhead_cost += amt

	# ─── Purchase Invoices tagged with this order ────────────────────────
	if delivery_acct:
		pi_del = _safe_table_query("tabPurchase Invoice", """
			SELECT IFNULL(SUM(pii.amount), 0) AS amt
			FROM `tabPurchase Invoice` pi
			INNER JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
			WHERE pi.docstatus = 1
			  AND pi.catering_order = %s
			  AND pii.expense_account = %s
		""", (catering_order, delivery_acct))
		if pi_del:
			delivery_cost += flt(pi_del[0].amt)

	# Rental — keyword match on Purchase Invoice items
	pi_rent = _safe_table_query("tabPurchase Invoice", """
		SELECT IFNULL(SUM(pii.amount), 0) AS amt
		FROM `tabPurchase Invoice` pi
		INNER JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
		WHERE pi.docstatus = 1
		  AND pi.catering_order = %s
		  AND (
			LOWER(IFNULL(pii.item_name,'')) LIKE '%%rental%%' OR
			LOWER(IFNULL(pii.item_name,'')) LIKE '%%rent%%' OR
			LOWER(IFNULL(pii.item_name,'')) LIKE '%%equipment%%' OR
			LOWER(IFNULL(pii.description,'')) LIKE '%%rental%%' OR
			LOWER(IFNULL(pii.description,'')) LIKE '%%hire%%'
		  )
	""", catering_order)
	if pi_rent:
		rental_cost += flt(pi_rent[0].amt)

	# ─── Wastage & Emergency → overhead ─────────────────────────────────
	wastage = _safe_sum_with_catering_link("tabCatering Wastage Entry",
		"total_wastage_value", catering_order)
	emergency = _safe_sum_with_catering_link("tabCatering Emergency Expense",
		"total_amount", catering_order)
	overhead_cost += flt(wastage) + flt(emergency)

	# ─── Totals ──────────────────────────────────────────────────────────
	total_cost = (flt(food_cost) + flt(beverage_cost) + flt(snacks_cost) +
	              flt(labor_cost) + flt(delivery_cost) + flt(rental_cost) +
	              flt(overhead_cost))
	gross_profit = flt(revenue) - flt(total_cost)
	margin = (gross_profit / revenue * 100) if revenue else 0

	return {
		"revenue":        flt(revenue),
		"invoiced":       flt(invoiced),
		"paid":           flt(paid),
		"outstanding":    flt(outstanding),
		"food_cost":      flt(food_cost),
		"beverage_cost":  flt(beverage_cost),
		"snacks_cost":    flt(snacks_cost),
		"labor_cost":     flt(labor_cost),
		"delivery_cost":  flt(delivery_cost),
		"rental_cost":    flt(rental_cost),
		"overhead_cost":  flt(overhead_cost),
		"wastage":        flt(wastage),     # informational, already in overhead
		"emergency":      flt(emergency),   # informational, already in overhead
		"cost":           flt(total_cost),  # alias kept for legacy JS card
		"total_cost":     flt(total_cost),
		"gross_profit":   flt(gross_profit),
		"gross_margin_percent": round(margin, 2),
		"currency":       currency,
	}


def _stock_consumption(catering_order, categories):
	"""Sum raw-material consumption from Stock Entries tagged with this
	Catering Order — ONLY from purpose='Manufacture' Stock Entries.

	Why Manufacture only:
	  Material Issue / Material Transfer for Manufacture / Repack may also
	  consume stock, but they represent intermediate steps that are typically
		captured downstream by Manufacture as the consumed materials are recorded.
	  Limiting to Manufacture avoids double-counting.

	Bucketing logic (with fallbacks):
	  1. If the consumed item is itself in Catering Order Items, use its category.
	  2. Else look up the Work Order's production_item in Catering Order Items.
	  3. Else default to "Food" (catch-all so consumption is never lost).
	"""
	try:
		cat_map = {}
		for r in frappe.db.sql("""
			SELECT item_code, category FROM `tabCatering Order Item`
			WHERE parent = %s AND item_code IS NOT NULL
		""", catering_order, as_dict=True):
			cat_map[r.item_code] = r.category or "Food"

		rows = frappe.db.sql("""
			SELECT se.work_order, sle.item_code AS sle_item,
			       ABS(sle.stock_value_difference) AS amt
			FROM `tabStock Ledger Entry` sle
			INNER JOIN `tabStock Entry` se ON se.name = sle.voucher_no
			WHERE sle.voucher_type = 'Stock Entry'
			  AND se.docstatus = 1
			  AND se.catering_order = %s
			  AND se.purpose = 'Manufacture'
			  AND sle.actual_qty < 0
		""", catering_order, as_dict=True)

		wo_cache = {}
		total = 0
		for row in rows:
			cat = None
			if row.sle_item in cat_map:
				cat = cat_map[row.sle_item]
			elif row.work_order:
				if row.work_order not in wo_cache:
					try:
						prod = frappe.db.get_value("Work Order",
							row.work_order, "production_item")
						wo_cache[row.work_order] = cat_map.get(prod) or "Food"
					except Exception:
						wo_cache[row.work_order] = "Food"
				cat = wo_cache[row.work_order]
			if not cat:
				cat = "Food"

			if cat in categories:
				total += flt(row.amt)
		return flt(total)
	except Exception:
		import traceback
		frappe.log_error(traceback.format_exc()[:400], "Stock Consumption Query")
		return 0



def _safe_table_query(table, sql, args):
	"""Run a query that depends on the catering_order column existing.
	Returns [] if the column doesn't exist."""
	try:
		check = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = %s
			  AND column_name = 'catering_order'
		""", table)
		if not check or check[0][0] == 0:
			return []
		return frappe.db.sql(sql, args, as_dict=True)
	except Exception:
		return []


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
	pkg_si = _get_package_sales_invoice(catering_order)
	if not pkg_si:
		frappe.throw(_("No package Sales Invoice exists yet. Create one first."))
	co.sales_invoice = pkg_si  # ensure we work with the package SI, not a service SI
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
		frappe.db.set_value("Catering Order", co.name, {
			"last_billed_snapshot": _compute_order_snapshot(co),
			"requires_rebill": 0,
		}, update_modified=False)
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

	# Include ALL prior package-related SIs for this order:
	# - Supplementary invoices (submitted, non-return, non-service)
	# - Credit notes (submitted, is_return=1, non-service) — their qty is negative
	# This guarantees billed_map reflects the FULL net billed-so-far per item.
	# Excludes additional service SIs (is_additional_service=1).
	try:
		has_flag_col = frappe.db.sql("""
			SELECT COUNT(*) FROM information_schema.columns
			WHERE table_schema = DATABASE()
			  AND table_name = 'tabSales Invoice'
			  AND column_name = 'is_additional_service'
		""")[0][0] > 0
	except Exception:
		has_flag_col = False

	service_filter = "AND IFNULL(si.is_additional_service, 0) = 0" if has_flag_col else ""
	prior_supps = frappe.db.sql(f"""
		SELECT sii.item_code, sii.qty, sii.rate, sii.item_name, sii.uom,
		       si.is_return
		FROM `tabSales Invoice Item` sii
		INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.catering_order = %s
		  AND si.name != %s
		  AND si.docstatus = 1
		  {service_filter}
	""", (catering_order, si.name), as_dict=True)

	for row in prior_supps:
		code = row.item_code
		# CRITICAL: add this item to billed_map even if it wasn't in the main SI.
		# (Previously this branch was guarded by `if code in billed_map` — which
		# caused the bug: new items from supplementaries got missed → next
		# Regenerate Bill click re-added them as a fresh supplementary.)
		if code in billed_map:
			billed_map[code]["qty"] += flt(row.qty)
		else:
			billed_map[code] = {
				"qty": flt(row.qty),
				"rate": flt(row.rate),
				"item_name": row.item_name,
				"uom": row.uom,
			}

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

	# Compute the DIFFERENCE per item — but ONLY for items that are part of
	# the catering order's items table. Anything billed that ISN'T in the order
	# (e.g. additional service items billed via the "Additional Service" button)
	# is intentionally ignored here — those have their own lifecycle and must
	# not trigger a credit note from Regenerate Bill.
	order_item_codes = set(current_map.keys())

	diffs = []  # list of {item_code, qty_diff (signed), rate, item_name, uom}
	for code in order_item_codes:
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
		frappe.db.set_value("Catering Order", co.name, {
			"last_billed_snapshot": _compute_order_snapshot(co),
			"requires_rebill": 0,
		}, update_modified=False)
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
	frappe.db.set_value("Catering Order", co.name, {
		"last_billed_snapshot": _compute_order_snapshot(co),
		"requires_rebill": 0,
	}, update_modified=False)

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



def _check_approval_gate(co):
	"""Raise if order is not submitted (docstatus < 1) or in terminal state."""
	if co.docstatus != 1:
		frappe.throw(_(
			"Cannot proceed: this Catering Order is in Draft. "
			"Click Submit at the top right to lock the order before creating any documents."
		), title=_("Order Not Submitted"))
	if co.status in ("Closed", "Void", "Cancelled"):
		frappe.throw(_("Cannot create new documents on a {0} Catering Order.").format(co.status),
			title=_("Order " + co.status))



MANAGER_ROLES = ("Catering Manager", "Catering Management",
                 "System Manager", "Administrator")


def _is_manager():
	return any(r in frappe.get_roles() for r in MANAGER_ROLES)


@frappe.whitelist()
def create_quick_expense(catering_order, expense_account, amount, expense_date,
                          entry_type="Cash", payee="", paid_from_account=None, supplier=None,
                          memo=None, reference_no=None):
	"""Create a Journal Entry for a quick expense linked to this Catering Order / Project.

	Two entry types:
	  - 'Cash'  : Dr Expense Account, Cr Bank/Cash (paid_from_account required)
	  - 'Bill'  : Dr Expense Account, Cr Accounts Payable (party=Supplier required;
	              creates an open payable that can be settled later via Payment Entry)
	"""
	co = frappe.get_doc("Catering Order", catering_order)
	if co.docstatus != 1:
		frappe.throw(_("Catering Order must be submitted before recording expenses."))

	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Amount must be greater than zero."))
	if not expense_account:
		frappe.throw(_("Expense Account is required."))

	entry_type = (entry_type or "Cash").strip()

	# Determine the credit (offset) account
	credit_account = None
	party_type = None
	party = None

	if entry_type == "Bill":
		# Payable to a Supplier
		if not supplier:
			frappe.throw(_("Supplier is required for Bill-type entries."))
		if not frappe.db.exists("Supplier", supplier):
			frappe.throw(_("Supplier {0} does not exist.").format(supplier))
		# Get payable account
		credit_account = frappe.db.get_value("Company", co.company, "default_payable_account")
		if not credit_account:
			frappe.throw(_("Set the Default Payable Account on Company {0}").format(co.company))
		party_type = "Supplier"
		party = supplier
		payee = supplier  # use supplier name as the payee
	else:
		# Cash / Bank entry
		if not paid_from_account:
			frappe.throw(_("Pay From Account is required for Cash-type entries."))
		if not payee:
			frappe.throw(_("Payee is required for Cash-type entries."))
		credit_account = paid_from_account
		if frappe.db.exists("Supplier", payee):
			party_type = "Supplier"
			party = payee

	je = frappe.new_doc("Journal Entry")
	if entry_type == "Bill":
		je.voucher_type = "Journal Entry"
	else:
		je.voucher_type = "Bank Entry" if "bank" in (credit_account or "").lower() else "Cash Entry"
	je.posting_date = expense_date or today()
	je.company = co.company
	je.cheque_no = reference_no or f"QE-{co.name}"
	je.cheque_date = expense_date or today()
	je.user_remark = f"Quick Expense ({entry_type}) for {co.name}: {memo or payee}"
	je.naming_series = _pick_naming_series_safe("Journal Entry")

	# Debit the expense account
	debit_row = {
		"account": expense_account,
		"debit_in_account_currency": amount,
		"cost_center": co.cost_center,
		"project": co.project,
		"user_remark": memo or f"Expense - {payee}",
	}
	je.append("accounts", debit_row)

	# Credit the offset account (Bank/Cash OR Payable with party)
	credit_row = {
		"account": credit_account,
		"credit_in_account_currency": amount,
		"cost_center": co.cost_center,
		"project": co.project,
		"user_remark": f"{'Payable to' if entry_type == 'Bill' else 'Paid'} {payee}",
	}
	if party_type and party:
		credit_row["party_type"] = party_type
		credit_row["party"] = party
	je.append("accounts", credit_row)

	je.flags.ignore_permissions = True
	je.insert()
	je.submit()

	frappe.db.set_value("Journal Entry", je.name, "catering_order", catering_order, update_modified=False)
	_log_activity(co, "Document Created",
		f"Quick Expense ({entry_type}) JE {je.name} for {payee}: {amount}",
		"Journal Entry", je.name)

	# Refresh the cost sheet immediately
	try:
		from dagaar_catering.catering_management.controllers.catering_cost_sheet import refresh_cost_sheet
		refresh_cost_sheet(catering_order)
	except Exception:
		pass

	return {
		"journal_entry": je.name,
		"amount": amount,
		"payee": payee,
		"entry_type": entry_type,
	}


@frappe.whitelist()
def get_quick_expense_defaults(catering_order):
	"""Return defaults for the Quick Expense popup."""
	co = frappe.get_doc("Catering Order", catering_order)
	settings = _get_settings() if "_get_settings" in globals() else None
	try:
		if not settings:
			settings = frappe.get_single("Catering Settings")
	except Exception:
		settings = None

	default_bank = None
	if settings:
		default_bank = settings.get("default_bank_account") or settings.get("default_cash_account")
	if not default_bank:
		default_bank = frappe.db.get_value("Company", co.company, "default_bank_account") or \
			frappe.db.get_value("Company", co.company, "default_cash_account")

	return {
		"company": co.company,
		"project": co.project,
		"currency": co.currency,
		"default_paid_from": default_bank,
		"default_date": today(),
	}


def _pick_naming_series_safe(doctype):
	try:
		meta = frappe.get_meta(doctype)
		field = meta.get_field("naming_series")
		if field and field.options:
			opts = [x.strip() for x in field.options.split("\n") if x.strip()]
			if opts:
				return opts[0]
	except Exception:
		pass
	return "ACC-JV-.YYYY.-" if doctype == "Journal Entry" else ""

@frappe.whitelist()
def bulk_delete_catering_orders(names):
	"""Force-delete Catering Orders along with all linked documents.

	Called from the list view bulk action. Cancels all linked docs first (via on_cancel
	cascade), then forcibly deletes the Catering Orders.

	Managers only.
	"""
	if not _is_manager():
		frappe.throw(_("Only Catering Manager can bulk-delete orders."), frappe.PermissionError)

	if isinstance(names, str):
		import json as _json
		try:
			names = _json.loads(names)
		except Exception:
			names = [names]

	deleted, failed = [], []
	for name in names:
		try:
			co = frappe.get_doc("Catering Order", name)
			# Trigger cancel cascade if submitted
			if co.docstatus == 1:
				co.flags.ignore_permissions = True
				co.flags.ignore_links = True
				co.cancel()
			# Now delete
			frappe.delete_doc("Catering Order", name, ignore_permissions=True, force=1,
				ignore_on_trash=True)
			deleted.append(name)
		except Exception as e:
			failed.append({"name": name, "error": str(e)[:200]})
			frappe.log_error(f"bulk_delete_catering_orders failed for {name}: {str(e)[:300]}",
				"Catering Bulk Delete")

	frappe.db.commit()
	return {"deleted": deleted, "failed": failed}

@frappe.whitelist()
def create_additional_service_invoice(catering_order, service_item, qty, rate, description=""):
	"""Create a SEPARATE Sales Invoice for an additional service charge.

	This is independent of the main order's SI — used for late-night surcharges,
	setup fees, extra-trip charges, etc. The service item must belong to item_group='Service'.

	The new SI is tagged with this catering_order so it shows up in revenue
	and profitability for the same order.
	"""
	co = frappe.get_doc("Catering Order", catering_order)
	if co.docstatus != 1:
		frappe.throw(_("Catering Order must be submitted before adding service charges."))
	if co.status in ("Closed", "Void", "Cancelled"):
		frappe.throw(_("Cannot add services to a {0} order.").format(co.status))

	# Validate the service item
	if not service_item:
		frappe.throw(_("Service Item is required."))
	item_group = frappe.db.get_value("Item", service_item, "item_group")
	if item_group != "Service":
		frappe.throw(_("Selected Item must belong to the 'Service' item group. Current group: {0}").format(item_group))

	qty = flt(qty)
	rate = flt(rate)
	if qty <= 0 or rate <= 0:
		frappe.throw(_("Quantity and Rate must be greater than zero."))

	# Create the Sales Invoice
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
	si.remarks = f"Additional service for Catering Order {co.name}: {description or service_item}"

	income_account = co.income_account or _settings_account("default_cogs_account")
	item_name = frappe.db.get_value("Item", service_item, "item_name") or service_item
	uom = frappe.db.get_value("Item", service_item, "stock_uom") or "Unit"

	si.append("items", {
		"item_code": service_item,
		"item_name": item_name,
		"qty": qty,
		"rate": rate,
		"uom": uom,
		"income_account": income_account,
		"cost_center": co.cost_center,
		"description": description or item_name,
	})

	si.is_additional_service = 1
	si.flags.ignore_permissions = True
	si.insert()
	try:
		si.submit()
	except Exception as e:
		frappe.msgprint(_("Service Invoice {0} created (Draft). Submit failed: {1}").format(
			si.name, str(e)[:200]), indicator="orange")

	_log_activity(co, "Document Created",
		f"Additional Service Invoice {si.name}: {item_name} × {qty} @ {rate}",
		"Sales Invoice", si.name)

	return si.name


def _settings_account(key):
	try:
		return frappe.db.get_single_value("Catering Settings", key)
	except Exception:
		return None

@frappe.whitelist()
def void_catering_order(catering_order, reason=None):
	"""Void a Catering Order before payment / production. Manager only."""
	if not _is_manager():
		frappe.throw(_("Only Catering Manager can void orders."), frappe.PermissionError)
	co = frappe.get_doc("Catering Order", catering_order)
	if co.status in ("Closed", "Cancelled", "Void"):
		frappe.throw(_("Order is already {0}.").format(co.status))
	if co.production_plan:
		frappe.throw(_("Cannot void: Production Plan exists. Use Cancel instead."))
	pay_count = frappe.db.count("Payment Entry",
		{"catering_order": catering_order, "docstatus": 1})
	if pay_count:
		frappe.throw(_("Cannot void: {0} Payment Entry(ies) already exist. "
		              "Use Cancel for this case.").format(pay_count))
	frappe.db.set_value("Catering Order", catering_order, {"status": "Void"},
		update_modified=True)
	_log_activity(co, "Order Voided",
		f"Voided by {frappe.session.user}" + (f" - {reason}" if reason else ""))
	frappe.msgprint(_("Catering Order voided."), indicator="orange", alert=True)
	return "Void"



def auto_void_stale_quotations():
	"""Scheduled (daily) — void any Catering Order whose quotation/SO is older
	than 30 days and has not been confirmed (no Sales Invoice created yet).

	Criteria for auto-void:
	  - docstatus = 1 (submitted)
	  - status NOT IN ('Closed', 'Cancelled', 'Void', 'Invoiced', 'Paid',
	                   'In Production', 'Delivered', 'Ready to Deliver')
	  - creation date > 30 days ago
	  - no Sales Invoice tagged
	  - no Production Plan tagged
	"""
	stale = frappe.db.sql("""
		SELECT name FROM `tabCatering Order`
		WHERE docstatus = 1
		  AND status NOT IN ('Closed', 'Cancelled', 'Void', 'Invoiced', 'Paid',
		                     'In Production', 'Delivered', 'Ready to Deliver',
		                     'Ready for Delivery', 'Deposit Received')
		  AND DATEDIFF(NOW(), creation) > 30
		  AND (sales_invoice IS NULL OR sales_invoice = '')
		  AND (production_plan IS NULL OR production_plan = '')
	""", as_dict=True)

	count = 0
	for row in stale:
		try:
			frappe.db.set_value("Catering Order", row.name, {
				"status": "Void",
			}, update_modified=False)
			frappe.get_doc({
				"doctype": "Catering Activity Log",
				"catering_order": row.name,
				"action": "Status Change",
				"details": "Auto-voided: 30+ days without confirmation",
				"action_by": "Administrator",
			}).insert(ignore_permissions=True)
			count += 1
		except Exception as e:
			frappe.log_error(f"Auto-void failed for {row.name}: {str(e)[:200]}",
				"Catering Auto-Void")

	if count:
		frappe.db.commit()
		frappe.logger().info(f"Catering Auto-Void: voided {count} stale orders")
	return count

@frappe.whitelist()
def get_items_for_package(menu_package):
	"""Return the items defined on a Catering Menu Package master.

	Used by the JS guest_count handler to identify which items in the
	order's items table came from THIS package (by item_code match).
	"""
	try:
		rows = frappe.db.sql("""
			SELECT item_code, qty_per_guest
			FROM `tabCatering Menu Package Item`
			WHERE parent = %s
		""", menu_package, as_dict=True)
		return rows
	except Exception:
		return []

@frappe.whitelist()
def check_items_invoiced(catering_order, item_codes):
	"""Return list of item codes that are already billed on a submitted
	non-additional-service Sales Invoice for this catering_order.

	Used by JS to block menu_package row removal when its items have been
	invoiced (books-already-out-of-sync protection).
	"""
	import json as _json
	if isinstance(item_codes, str):
		try:
			item_codes = _json.loads(item_codes)
		except Exception:
			item_codes = [item_codes]
	if not item_codes:
		return []

	rows = frappe.db.sql("""
		SELECT DISTINCT sii.item_code
		FROM `tabSales Invoice Item` sii
		INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.catering_order = %s
		  AND si.docstatus = 1
		  AND IFNULL(si.is_additional_service, 0) = 0
		  AND IFNULL(si.is_return, 0) = 0
		  AND sii.item_code IN %s
	""", (catering_order, tuple(item_codes)), as_dict=True)
	return [r.item_code for r in rows]

@frappe.whitelist()
def check_work_orders_complete(catering_order):
	"""Return work-order completion status for this catering order.

	A Work Order is considered "complete" when status='Completed'
	(produced_qty == qty). Used by the Delivery Plan button gate to
	prevent premature delivery planning.

	Returns: { total, completed, all_complete }
	"""
	rows = frappe.db.sql("""
		SELECT name, status, qty, produced_qty
		FROM `tabWork Order`
		WHERE catering_order = %s
		  AND docstatus = 1
	""", catering_order, as_dict=True)
	total = len(rows)
	completed = sum(1 for r in rows if (r.status or "") == "Completed"
	                                or flt(r.produced_qty or 0) >= flt(r.qty or 0) > 0)
	return {
		"total": total,
		"completed": completed,
		"all_complete": (total > 0 and completed == total),
	}

