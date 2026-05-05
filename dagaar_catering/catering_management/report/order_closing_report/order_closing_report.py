import frappe
from frappe import _
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("Closing Sheet"), "fieldname": "closing_sheet", "fieldtype": "Link", "options": "Catering Closing Sheet", "width": 160},
        {"label": _("Order"), "fieldname": "catering_order", "fieldtype": "Link", "options": "Catering Order", "width": 150},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Data", "width": 140},
        {"label": _("Event Date"), "fieldname": "event_date", "fieldtype": "Date", "width": 100},
        {"label": _("Closing Date"), "fieldname": "closing_date", "fieldtype": "Date", "width": 110},
        {"label": _("Total Revenue"), "fieldname": "total_revenue", "fieldtype": "Currency", "width": 120},
        {"label": _("Total Cost"), "fieldname": "total_cost", "fieldtype": "Currency", "width": 110},
        {"label": _("Net Profit"), "fieldname": "net_profit", "fieldtype": "Currency", "width": 110},
        {"label": _("Margin %"), "fieldname": "margin_pct", "fieldtype": "Percent", "width": 90},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
    ]
    cond = "cs.docstatus=1"
    if filters.get("from_date"): cond += " AND cs.closing_date >= %(from_date)s"
    if filters.get("to_date"): cond += " AND cs.closing_date <= %(to_date)s"
    if filters.get("company"): cond += " AND cs.company = %(company)s"
    data = frappe.db.sql(f"""
        SELECT cs.name AS closing_sheet, cs.catering_order, co.customer,
            co.event_date, cs.closing_date, cs.total_revenue, cs.total_cost,
            cs.net_profit, cs.gross_margin_percent AS margin_pct, cs.status
        FROM `tabCatering Closing Sheet` cs
        LEFT JOIN `tabCatering Order` co ON co.name=cs.catering_order
        WHERE {cond} ORDER BY cs.closing_date DESC
    """, filters, as_dict=True)
    return columns, data
