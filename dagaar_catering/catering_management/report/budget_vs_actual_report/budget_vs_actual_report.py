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
		{"label": _("Budget Revenue"), "fieldname": "budget_revenue", "fieldtype": "Currency", "width": 130},
		{"label": _("Actual Revenue"), "fieldname": "actual_revenue", "fieldtype": "Currency", "width": 130},
		{"label": _("Budget Cost"), "fieldname": "budget_cost", "fieldtype": "Currency", "width": 120},
		{"label": _("Actual Cost"), "fieldname": "actual_cost", "fieldtype": "Currency", "width": 120},
		{"label": _("Variance"), "fieldname": "variance", "fieldtype": "Currency", "width": 120},
		{"label": _("Variance %"), "fieldname": "variance_pct", "fieldtype": "Percent", "width": 100},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
	]


def get_data(filters):
	cond = "co.docstatus=1"
	if filters.get("from_date"): cond += " AND co.event_date>=%(from_date)s"
	if filters.get("to_date"):   cond += " AND co.event_date<=%(to_date)s"
	if filters.get("company"):   cond += " AND co.company=%(company)s"

	orders = frappe.db.sql(f"""
		SELECT co.name AS order_name, co.customer, co.event_date,
			co.total_order_value AS budget_revenue, co.status
		FROM `tabCatering Order` co WHERE {cond} ORDER BY co.event_date DESC
	""", filters, as_dict=True)

	data = []
	for o in orders:
		actual_rev = frappe.db.sql("SELECT IFNULL(SUM(grand_total),0) FROM `tabSales Invoice` WHERE docstatus=1 AND catering_order=%s", o.order_name)[0][0]
		budget_cost = frappe.db.sql("SELECT IFNULL(SUM(total_cost),0) FROM `tabCatering Cost Sheet` WHERE docstatus=1 AND catering_order=%s", o.order_name)[0][0]
		actual_cost = frappe.db.sql("SELECT IFNULL(SUM(total_wastage_value),0)+0 FROM `tabCatering Wastage Entry` WHERE docstatus=1 AND catering_order=%s", o.order_name)[0][0]
		emergency = frappe.db.sql("SELECT IFNULL(SUM(total_amount),0) FROM `tabCatering Emergency Expense` WHERE docstatus=1 AND catering_order=%s", o.order_name)[0][0]
		actual_cost_total = flt(budget_cost) + flt(actual_cost) + flt(emergency)
		variance = flt(budget_cost) - actual_cost_total
		vpct = (variance / flt(budget_cost) * 100) if flt(budget_cost) else 0
		data.append({**o, "actual_revenue": flt(actual_rev), "budget_cost": flt(budget_cost),
			"actual_cost": actual_cost_total, "variance": variance, "variance_pct": round(vpct, 2)})
	return data
