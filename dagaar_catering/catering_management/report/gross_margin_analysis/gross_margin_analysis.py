import frappe
from frappe import _
from frappe.utils import flt
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("Period"), "fieldname": "period", "fieldtype": "Data", "width": 100},
        {"label": _("Orders"), "fieldname": "order_count", "fieldtype": "Int", "width": 70},
        {"label": _("Revenue"), "fieldname": "revenue", "fieldtype": "Currency", "width": 120},
        {"label": _("COGS"), "fieldname": "cogs", "fieldtype": "Currency", "width": 120},
        {"label": _("Gross Profit"), "fieldname": "gross_profit", "fieldtype": "Currency", "width": 120},
        {"label": _("Gross Margin %"), "fieldname": "gross_margin_pct", "fieldtype": "Percent", "width": 120},
        {"label": _("Wastage"), "fieldname": "wastage", "fieldtype": "Currency", "width": 110},
        {"label": _("Net Margin %"), "fieldname": "net_margin_pct", "fieldtype": "Percent", "width": 110},
        {"label": _("Target Margin %"), "fieldname": "target_margin", "fieldtype": "Percent", "width": 120},
        {"label": _("Variance vs Target"), "fieldname": "margin_variance", "fieldtype": "Percent", "width": 130},
    ]
    cond = "co.docstatus=1"
    if filters.get("from_date"): cond += " AND co.event_date >= %(from_date)s"
    if filters.get("to_date"): cond += " AND co.event_date <= %(to_date)s"
    if filters.get("company"): cond += " AND co.company = %(company)s"
    try: target_margin = flt(frappe.db.get_single_value("Catering Settings","minimum_margin_percent") or 20)
    except: target_margin = 20.0
    rows = frappe.db.sql(f"""
        SELECT DATE_FORMAT(co.event_date,'%b %Y') AS period, DATE_FORMAT(co.event_date,'%Y-%m') AS period_key,
            COUNT(co.name) AS order_count, SUM(co.total_order_value) AS revenue
        FROM `tabCatering Order` co WHERE {cond}
        GROUP BY period_key ORDER BY period_key
    """, filters, as_dict=True)
    data = []
    for r in rows:
        cogs = frappe.db.sql("""SELECT IFNULL(SUM(cs.total_cost),0) FROM `tabCatering Cost Sheet` cs JOIN `tabCatering Order` co ON co.name=cs.catering_order WHERE cs.docstatus=1 AND DATE_FORMAT(co.event_date,'%Y-%m')=%s""", r.period_key)[0][0]
        wastage = frappe.db.sql("""SELECT IFNULL(SUM(we.total_wastage_value),0) FROM `tabCatering Wastage Entry` we JOIN `tabCatering Order` co ON co.name=we.catering_order WHERE we.docstatus=1 AND DATE_FORMAT(co.event_date,'%Y-%m')=%s""", r.period_key)[0][0]
        rev = flt(r.revenue); cv = flt(cogs); wv = flt(wastage)
        gp = rev - cv; nm = gp - wv
        gm = (gp/rev*100) if rev else 0; nmm = (nm/rev*100) if rev else 0
        data.append({"period": r.period,"order_count": r.order_count,"revenue": rev,"cogs": cv,"gross_profit": gp,"gross_margin_pct": round(gm,2),"wastage": wv,"net_margin_pct": round(nmm,2),"target_margin": target_margin,"margin_variance": round(gm-target_margin,2)})
    return columns, data
