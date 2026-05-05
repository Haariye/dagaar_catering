import frappe
from frappe import _
from frappe.utils import flt
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("Menu/Package Type"), "fieldname": "package_type", "fieldtype": "Data", "width": 160},
        {"label": _("Orders"), "fieldname": "order_count", "fieldtype": "Int", "width": 80},
        {"label": _("Total Guests"), "fieldname": "total_guests", "fieldtype": "Int", "width": 100},
        {"label": _("Total Revenue"), "fieldname": "total_revenue", "fieldtype": "Currency", "width": 130},
        {"label": _("Avg per Guest"), "fieldname": "avg_per_guest", "fieldtype": "Currency", "width": 120},
        {"label": _("Total Cost"), "fieldname": "total_cost", "fieldtype": "Currency", "width": 120},
        {"label": _("Gross Profit"), "fieldname": "gross_profit", "fieldtype": "Currency", "width": 120},
        {"label": _("Margin %"), "fieldname": "margin_pct", "fieldtype": "Percent", "width": 90},
    ]
    cond = "co.docstatus=1"
    if filters.get("from_date"): cond += f" AND co.event_date >= '{filters['from_date']}'"
    if filters.get("to_date"): cond += f" AND co.event_date <= '{filters['to_date']}'"
    rows = frappe.db.sql(f"SELECT IFNULL(co.package_type,'Unspecified') AS package_type, COUNT(co.name) AS order_count, SUM(co.total_guests) AS total_guests, SUM(co.total_order_value) AS total_revenue FROM `tabCatering Order` co WHERE {cond} GROUP BY co.package_type ORDER BY total_revenue DESC", as_dict=True)
    data = []
    for r in rows:
        tc = frappe.db.sql("SELECT IFNULL(SUM(cs.total_cost),0) FROM `tabCatering Cost Sheet` cs JOIN `tabCatering Order` co ON co.name=cs.catering_order WHERE cs.docstatus=1 AND IFNULL(co.package_type,'Unspecified')=%s", r.package_type)[0][0]
        rev = flt(r.total_revenue); cost = flt(tc); gp = rev-cost; g = flt(r.total_guests) or 1
        data.append({"package_type": r.package_type,"order_count": r.order_count,"total_guests": int(g),"total_revenue": rev,"avg_per_guest": round(rev/g,2),"total_cost": cost,"gross_profit": gp,"margin_pct": round((gp/rev*100) if rev else 0,2)})
    return columns, data
