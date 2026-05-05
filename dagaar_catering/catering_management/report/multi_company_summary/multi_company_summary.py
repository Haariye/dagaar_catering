import frappe
from frappe import _
from frappe.utils import flt
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 180},
        {"label": _("Orders"), "fieldname": "order_count", "fieldtype": "Int", "width": 80},
        {"label": _("Total Revenue"), "fieldname": "total_revenue", "fieldtype": "Currency", "width": 130},
        {"label": _("Total Cost"), "fieldname": "total_cost", "fieldtype": "Currency", "width": 120},
        {"label": _("Gross Profit"), "fieldname": "gross_profit", "fieldtype": "Currency", "width": 120},
        {"label": _("Margin %"), "fieldname": "margin_pct", "fieldtype": "Percent", "width": 90},
        {"label": _("Receivable"), "fieldname": "receivable", "fieldtype": "Currency", "width": 120},
        {"label": _("Payable"), "fieldname": "payable", "fieldtype": "Currency", "width": 120},
    ]
    cond = "co.docstatus=1"
    if filters.get("from_date"): cond += f" AND co.event_date >= '{filters['from_date']}'"
    if filters.get("to_date"): cond += f" AND co.event_date <= '{filters['to_date']}'"
    companies = frappe.db.sql(f"SELECT co.company, COUNT(co.name) AS order_count, SUM(co.total_order_value) AS total_revenue FROM `tabCatering Order` co WHERE {cond} GROUP BY co.company ORDER BY co.company", as_dict=True)
    data = []
    for c in companies:
        tc = frappe.db.sql("""SELECT IFNULL(SUM(cs.total_cost),0) FROM `tabCatering Cost Sheet` cs JOIN `tabCatering Order` co ON co.name=cs.catering_order WHERE cs.docstatus=1 AND co.company=%s AND co.docstatus=1""", c.company)[0][0]
        ar = frappe.db.sql("SELECT IFNULL(SUM(outstanding_amount),0) FROM `tabSales Invoice` WHERE docstatus=1 AND company=%s AND status IN ('Unpaid','Partly Paid','Overdue')", c.company)[0][0]
        ap = frappe.db.sql("SELECT IFNULL(SUM(outstanding_amount),0) FROM `tabPurchase Invoice` WHERE docstatus=1 AND company=%s AND status IN ('Unpaid','Partly Paid','Overdue')", c.company)[0][0]
        rev = flt(c.total_revenue); cost = flt(tc); gp = rev-cost
        data.append({"company": c.company,"order_count": c.order_count,"total_revenue": rev,"total_cost": cost,"gross_profit": gp,"margin_pct": round((gp/rev*100) if rev else 0,2),"receivable": flt(ar),"payable": flt(ap)})
    return columns, data
