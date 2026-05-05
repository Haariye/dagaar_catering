# Copyright (c) 2024, DagaarSoft
import frappe
from frappe import _
from frappe.utils import flt

def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)

def get_columns():
    return [
        {"label": _("Order"), "fieldname": "order_name", "fieldtype": "Link", "options": "Catering Order", "width": 160},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Data", "width": 150},
        {"label": _("Event Date"), "fieldname": "event_date", "fieldtype": "Date", "width": 100},
        {"label": _("Revenue"), "fieldname": "revenue", "fieldtype": "Currency", "width": 120},
        {"label": _("Food Cost"), "fieldname": "food_cost", "fieldtype": "Currency", "width": 110},
        {"label": _("Beverage Cost"), "fieldname": "beverage_cost", "fieldtype": "Currency", "width": 120},
        {"label": _("Labor Cost"), "fieldname": "labor_cost", "fieldtype": "Currency", "width": 110},
        {"label": _("Delivery Cost"), "fieldname": "delivery_cost", "fieldtype": "Currency", "width": 115},
        {"label": _("Wastage"), "fieldname": "wastage_cost", "fieldtype": "Currency", "width": 100},
        {"label": _("Emergency Exp"), "fieldname": "emergency_cost", "fieldtype": "Currency", "width": 120},
        {"label": _("Total Expenses"), "fieldname": "total_expenses", "fieldtype": "Currency", "width": 130},
        {"label": _("Gross Profit"), "fieldname": "gross_profit", "fieldtype": "Currency", "width": 120},
        {"label": _("Gross Margin %"), "fieldname": "gross_margin", "fieldtype": "Percent", "width": 110},
        {"label": _("Net Profit"), "fieldname": "net_profit", "fieldtype": "Currency", "width": 110},
        {"label": _("Net Margin %"), "fieldname": "net_margin", "fieldtype": "Percent", "width": 100},
    ]

def get_data(filters):
    cond = "co.docstatus=1"
    if filters.get("from_date"): cond += " AND co.event_date >= %(from_date)s"
    if filters.get("to_date"): cond += " AND co.event_date <= %(to_date)s"
    if filters.get("company"): cond += " AND co.company = %(company)s"
    orders = frappe.db.sql(f"SELECT co.name AS order_name, co.customer, co.event_date, co.total_order_value AS revenue FROM `tabCatering Order` co WHERE {cond} ORDER BY co.event_date DESC", filters, as_dict=True)
    data = []
    for o in orders:
        cs = frappe.db.get_value("Catering Cost Sheet",{"catering_order": o.order_name,"docstatus": 1},["food_cost","beverage_cost","snacks_cost","packaging_cost","labor_cost","delivery_cost","total_cost"],as_dict=True) or {}
        wastage = frappe.db.sql("SELECT IFNULL(SUM(total_wastage_value),0) FROM `tabCatering Wastage Entry` WHERE docstatus=1 AND catering_order=%s", o.order_name)[0][0]
        emergency = frappe.db.sql("SELECT IFNULL(SUM(total_amount),0) FROM `tabCatering Emergency Expense` WHERE docstatus=1 AND catering_order=%s", o.order_name)[0][0]
        food = flt(cs.get("food_cost",0)) + flt(cs.get("snacks_cost",0)) + flt(cs.get("packaging_cost",0))
        bev = flt(cs.get("beverage_cost",0)); labor = flt(cs.get("labor_cost",0)); deliv = flt(cs.get("delivery_cost",0))
        wast = flt(wastage); emerg = flt(emergency)
        total_exp = food + bev + labor + deliv + wast + emerg
        rev = flt(o.revenue); gross = rev - (food + bev + labor + deliv); net = rev - total_exp
        data.append({"order_name": o.order_name,"customer": o.customer,"event_date": o.event_date,"revenue": rev,"food_cost": food,"beverage_cost": bev,"labor_cost": labor,"delivery_cost": deliv,"wastage_cost": wast,"emergency_cost": emerg,"total_expenses": total_exp,"gross_profit": gross,"gross_margin": round((gross/rev*100) if rev else 0,2),"net_profit": net,"net_margin": round((net/rev*100) if rev else 0,2)})
    return data
