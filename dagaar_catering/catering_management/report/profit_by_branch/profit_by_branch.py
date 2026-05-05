import frappe
from frappe import _
from frappe.utils import flt
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("Branch"), "fieldname": "branch", "fieldtype": "Data", "width": 150},
        {"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 150},
        {"label": _("Orders"), "fieldname": "order_count", "fieldtype": "Int", "width": 80},
        {"label": _("Total Revenue"), "fieldname": "total_revenue", "fieldtype": "Currency", "width": 130},
        {"label": _("Total Cost"), "fieldname": "total_cost", "fieldtype": "Currency", "width": 120},
        {"label": _("Gross Profit"), "fieldname": "gross_profit", "fieldtype": "Currency", "width": 120},
        {"label": _("Margin %"), "fieldname": "margin_pct", "fieldtype": "Percent", "width": 90},
    ]
    cond = "co.docstatus=1"
    if filters.get("from_date"): cond += f" AND co.event_date >= '{filters['from_date']}'"
    if filters.get("to_date"): cond += f" AND co.event_date <= '{filters['to_date']}'"
    if filters.get("company"): cond += f" AND co.company = '{filters['company']}'"
    rows = frappe.db.sql(f"SELECT IFNULL(co.branch,'Head Office') AS branch, co.company, COUNT(co.name) AS order_count, SUM(co.total_order_value) AS total_revenue FROM `tabCatering Order` co WHERE {cond} GROUP BY co.branch, co.company ORDER BY total_revenue DESC", as_dict=True)
    data = []
    for r in rows:
        tc = frappe.db.sql("SELECT IFNULL(SUM(cs.total_cost),0) FROM `tabCatering Cost Sheet` cs JOIN `tabCatering Order` co ON co.name=cs.catering_order WHERE cs.docstatus=1 AND IFNULL(co.branch,'Head Office')=%s AND co.company=%s AND co.docstatus=1", (r.branch, r.company))[0][0]
        rev = flt(r.total_revenue); cost = flt(tc); gp = rev-cost
        data.append({"branch": r.branch,"company": r.company,"order_count": r.order_count,"total_revenue": rev,"total_cost": cost,"gross_profit": gp,"margin_pct": round((gp/rev*100) if rev else 0,2)})
    return columns, data
