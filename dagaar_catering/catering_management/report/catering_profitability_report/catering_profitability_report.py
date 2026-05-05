# Copyright (c) 2024, DagaarSoft
import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	return get_columns(), get_data(filters or {})


def get_columns():
	return [
		{"label": _("Order"), "fieldname": "order_name", "fieldtype": "Link", "options": "Catering Order", "width": 160},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Data", "width": 150},
		{"label": _("Event Date"), "fieldname": "event_date", "fieldtype": "Date", "width": 100},
		{"label": _("Guests"), "fieldname": "total_guests", "fieldtype": "Int", "width": 70},
		{"label": _("Package Type"), "fieldname": "package_type", "fieldtype": "Data", "width": 110},
		{"label": _("Revenue"), "fieldname": "revenue", "fieldtype": "Currency", "width": 120},
		{"label": _("Total Cost"), "fieldname": "total_cost", "fieldtype": "Currency", "width": 120},
		{"label": _("Gross Profit"), "fieldname": "gross_profit", "fieldtype": "Currency", "width": 120},
		{"label": _("Margin %"), "fieldname": "gross_margin_pct", "fieldtype": "Percent", "width": 90},
		{"label": _("Wastage"), "fieldname": "wastage", "fieldtype": "Currency", "width": 100},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
	]


def get_data(filters):
	cond = "co.docstatus=1"
	if filters.get("from_date"): cond += " AND co.event_date>=%(from_date)s"
	if filters.get("to_date"):   cond += " AND co.event_date<=%(to_date)s"
	if filters.get("company"):   cond += " AND co.company=%(company)s"
	if filters.get("status"):    cond += " AND co.status=%(status)s"

	orders = frappe.db.sql(f"""
		SELECT co.name AS order_name, co.customer, co.event_date,
			co.total_guests, co.package_type, co.total_order_value AS revenue, co.status
		FROM `tabCatering Order` co WHERE {cond} ORDER BY co.event_date DESC
	""", filters, as_dict=True)

	data = []
	for o in orders:
		cost = frappe.db.sql("SELECT IFNULL(SUM(total_cost),0) FROM `tabCatering Cost Sheet` WHERE docstatus=1 AND catering_order=%s", o.order_name)[0][0]
		waste = frappe.db.sql("SELECT IFNULL(SUM(total_wastage_value),0) FROM `tabCatering Wastage Entry` WHERE docstatus=1 AND catering_order=%s", o.order_name)[0][0]
		rev = flt(o.revenue)
		tc = flt(cost) + flt(waste)
		profit = rev - tc
		margin = (profit / rev * 100) if rev else 0
		data.append({**o, "total_cost": tc, "gross_profit": profit, "gross_margin_pct": round(margin, 2), "wastage": flt(waste)})
	return data
