# Copyright (c) 2024, DagaarSoft — Daily scheduled tasks

import frappe
from frappe import _
from frappe.utils import today, add_days


def execute():
	check_orders_without_deposit()
	check_overdue_payments()
	check_production_not_started()
	check_orders_due_tomorrow()


def check_orders_without_deposit():
	rows = frappe.db.sql("""
		SELECT name, customer, event_date, total_order_value, deposit_amount
		FROM `tabCatering Order`
		WHERE docstatus=1 AND status IN ('Sales Order Created','Confirmed')
		AND (deposit_received IS NULL OR deposit_received=0)
		AND event_date >= %s
	""", today(), as_dict=True)
	for r in rows:
		_notify(_get_role_users("Catering Finance Manager"),
			_("Deposit Pending: {0}").format(r.name),
			_("Order {0} for {1} (Event: {2}) has no deposit received. Required: {3}").format(
				r.name, r.customer, r.event_date, r.deposit_amount),
			"Catering Order", r.name)


def check_overdue_payments():
	rows = frappe.db.sql("""
		SELECT co.name, co.customer, ps.due_date, ps.expected_amount, ps.payment_type
		FROM `tabCatering Order` co
		JOIN `tabCatering Order Payment Schedule` ps ON ps.parent=co.name
		WHERE co.docstatus=1 AND co.status NOT IN ('Closed','Cancelled')
		AND ps.received=0 AND ps.due_date < %s
	""", today(), as_dict=True)
	for r in rows:
		_notify(_get_role_users("Catering Finance User"),
			_("Overdue Payment: {0}").format(r.name),
			_("{0} payment of {1} was due {2} for Order {3}/{4}").format(
				r.payment_type, r.expected_amount, r.due_date, r.name, r.customer),
			"Catering Order", r.name)


def check_production_not_started():
	threshold = add_days(today(), 3)
	rows = frappe.db.sql("""
		SELECT name, customer, event_date FROM `tabCatering Order`
		WHERE docstatus=1 AND status IN ('Sales Order Created','Deposit Received')
		AND event_date <= %s AND event_date >= %s
		AND (production_plan IS NULL OR production_plan='')
	""", (threshold, today()), as_dict=True)
	for r in rows:
		_notify(_get_role_users("Catering Kitchen Manager"),
			_("Production Not Started: {0}").format(r.name),
			_("Order {0} for {1} is due {2} but Production Plan not yet created.").format(
				r.name, r.customer, r.event_date),
			"Catering Order", r.name)


def check_orders_due_tomorrow():
	tomorrow = add_days(today(), 1)
	rows = frappe.db.sql("""
		SELECT name, customer, event_location, total_guests FROM `tabCatering Order`
		WHERE docstatus=1 AND status NOT IN ('Closed','Cancelled','Delivered')
		AND event_date=%s
	""", tomorrow, as_dict=True)
	for r in rows:
		recipients = _get_role_users("Catering Kitchen Manager") + _get_role_users("Catering Delivery User")
		_notify(recipients,
			_("Event TOMORROW: {0}").format(r.name),
			_("Order {0} for {1} is TOMORROW at {2}. Total guests: {3}").format(
				r.name, r.customer, r.event_location or "TBD", r.total_guests),
			"Catering Order", r.name)


def _get_role_users(role):
	rows = frappe.db.sql("""
		SELECT DISTINCT u.email FROM `tabUser` u
		JOIN `tabHas Role` hr ON hr.parent=u.name
		WHERE hr.role=%s AND u.enabled=1
	""", role, as_dict=True)
	return [r.email for r in rows if r.email]


def _notify(recipients, subject, message, ref_dt=None, ref_name=None):
	if not recipients:
		return
	try:
		frappe.sendmail(
			recipients=list(set(recipients)),
			subject=subject,
			message=f"<p>{message}</p>",
			reference_doctype=ref_dt,
			reference_name=ref_name,
			now=False,
		)
	except Exception as e:
		frappe.log_error(f"DagaarSoft notify error: {e}")
