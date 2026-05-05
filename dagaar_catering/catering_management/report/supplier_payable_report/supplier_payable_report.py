import frappe
from frappe import _
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 160},
        {"label": _("Supplier Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 160},
        {"label": _("Purchase Invoice"), "fieldname": "pi_name", "fieldtype": "Link", "options": "Purchase Invoice", "width": 160},
        {"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
        {"label": _("Due Date"), "fieldname": "due_date", "fieldtype": "Date", "width": 100},
        {"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 120},
        {"label": _("Outstanding"), "fieldname": "outstanding_amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
        {"label": _("Overdue Days"), "fieldname": "overdue_days", "fieldtype": "Int", "width": 100},
    ]
    cond = "pi.docstatus=1 AND pi.status IN ('Unpaid','Partly Paid','Overdue')"
    if filters.get("from_date"): cond += " AND pi.posting_date >= %(from_date)s"
    if filters.get("to_date"): cond += " AND pi.posting_date <= %(to_date)s"
    if filters.get("company"): cond += " AND pi.company = %(company)s"
    data = frappe.db.sql(f"SELECT pi.name AS pi_name, pi.supplier, pi.supplier_name, pi.posting_date, pi.due_date, pi.grand_total, pi.outstanding_amount, pi.status, DATEDIFF(CURDATE(),pi.due_date) AS overdue_days FROM `tabPurchase Invoice` pi WHERE {cond} ORDER BY pi.due_date", filters, as_dict=True)
    return columns, data
