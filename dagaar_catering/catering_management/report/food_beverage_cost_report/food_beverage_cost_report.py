import frappe
from frappe import _
from frappe.utils import flt
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("Order"), "fieldname": "order_name", "fieldtype": "Link", "options": "Catering Order", "width": 150},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Data", "width": 140},
        {"label": _("Event Date"), "fieldname": "event_date", "fieldtype": "Date", "width": 100},
        {"label": _("Guests"), "fieldname": "total_guests", "fieldtype": "Int", "width": 80},
        {"label": _("Food Cost"), "fieldname": "food_cost", "fieldtype": "Currency", "width": 110},
        {"label": _("Bev Cost"), "fieldname": "beverage_cost", "fieldtype": "Currency", "width": 110},
        {"label": _("Labor Cost"), "fieldname": "labor_cost", "fieldtype": "Currency", "width": 110},
        {"label": _("Total Cost"), "fieldname": "total_cost", "fieldtype": "Currency", "width": 110},
        {"label": _("Cost/Guest"), "fieldname": "cost_per_guest", "fieldtype": "Currency", "width": 100},
        {"label": _("Revenue"), "fieldname": "revenue", "fieldtype": "Currency", "width": 110},
        {"label": _("Margin %"), "fieldname": "margin_pct", "fieldtype": "Percent", "width": 90},
    ]
    cond = "co.docstatus=1"
    if filters.get("from_date"): cond += " AND co.event_date >= %(from_date)s"
    if filters.get("to_date"): cond += " AND co.event_date <= %(to_date)s"
    orders = frappe.db.sql(f"SELECT co.name AS order_name, co.customer, co.event_date, co.total_guests, co.total_order_value AS revenue FROM `tabCatering Order` co WHERE {cond} ORDER BY co.event_date DESC", filters, as_dict=True)
    data = []
    for o in orders:
        cs = frappe.db.get_value("Catering Cost Sheet",{"catering_order": o.order_name,"docstatus": 1},["food_cost","beverage_cost","labor_cost","total_cost"],as_dict=True) or {}
        tc = flt(cs.get("total_cost",0)); rev = flt(o.revenue); g = flt(o.total_guests) or 1
        data.append({"order_name": o.order_name,"customer": o.customer,"event_date": o.event_date,"total_guests": int(g),"food_cost": flt(cs.get("food_cost",0)),"beverage_cost": flt(cs.get("beverage_cost",0)),"labor_cost": flt(cs.get("labor_cost",0)),"total_cost": tc,"cost_per_guest": round(tc/g,2),"revenue": rev,"margin_pct": round((rev-tc)/rev*100 if rev else 0,2)})
    return columns, data
