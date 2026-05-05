# Copyright (c) 2024, DagaarSoft
import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	return get_columns(), get_data(filters or {})


def get_columns():
	return [
		{"label": _("Order"), "fieldname": "order_name", "fieldtype": "Link", "options": "Catering Order", "width": 160},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 150},
		{"label": _("Event Date"), "fieldname": "event_date", "fieldtype": "Date", "width": 100},
		{"label": _("Order Value"), "fieldname": "order_value", "fieldtype": "Currency", "width": 120},
		{"label": _("Invoiced"), "fieldname": "invoiced", "fieldtype": "Currency", "width": 120},
		{"label": _("Received"), "fieldname": "received", "fieldtype": "Currency", "width": 120},
		{"label": _("Outstanding"), "fieldname": "outstanding", "fieldtype": "Currency", "width": 120},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
	]


def get_data(filters):
	cond = "co.docstatus=1"
	if filters.get("from_date"): cond += " AND co.event_date>=%(from_date)s"
	if filters.get("to_date"):   cond += " AND co.event_date<=%(to_date)s"
	if filters.get("company"):   cond += " AND co.company=%(company)s"
	if filters.get("customer"):  cond += " AND co.customer=%(customer)s"

	orders = frappe.db.sql(f"""
		SELECT co.name AS order_name, co.customer, co.event_date,
			co.total_order_value AS order_value, co.status
		FROM `tabCatering Order` co WHERE {cond} ORDER BY co.event_date DESC
	""", filters, as_dict=True)

	data = []
	for o in orders:
		invoiced = frappe.db.sql("SELECT IFNULL(SUM(grand_total),0) FROM `tabSales Invoice` WHERE docstatus=1 AND catering_order=%s", o.order_name)[0][0]
		received = frappe.db.sql("SELECT IFNULL(SUM(paid_amount),0) FROM `tabPayment Entry` WHERE docstatus=1 AND payment_type='Receive' AND reference_doctype='Catering Order' AND reference_name=%s", o.order_name)[0][0]
		outstanding = flt(invoiced) - flt(received)
		data.append({**o, "invoiced": flt(invoiced), "received": flt(received), "outstanding": outstanding})
	return data
