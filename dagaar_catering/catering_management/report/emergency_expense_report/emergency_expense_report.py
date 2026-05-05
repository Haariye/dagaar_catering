import frappe
from frappe import _
def execute(filters=None):
    filters = filters or {}
    return [
        {"label": _("Document"), "fieldname": "name", "fieldtype": "Link", "options": "Catering Emergency Expense", "width": 150},
        {"label": _("Order"), "fieldname": "catering_order", "fieldtype": "Link", "options": "Catering Order", "width": 150},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Data", "width": 140},
        {"label": _("Date"), "fieldname": "expense_date", "fieldtype": "Date", "width": 100},
        {"label": _("Category"), "fieldname": "expense_category", "fieldtype": "Data", "width": 130},
        {"label": _("Total Amount"), "fieldname": "total_amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Approval Status"), "fieldname": "approval_status", "fieldtype": "Data", "width": 120},
        {"label": _("Approved By"), "fieldname": "approved_by", "fieldtype": "Data", "width": 130},
    ], frappe.db.sql("""
        SELECT ee.name, ee.catering_order, co.customer, ee.expense_date,
            ee.expense_category, ee.total_amount, ee.approval_status, ee.approved_by
        FROM `tabCatering Emergency Expense` ee
        LEFT JOIN `tabCatering Order` co ON co.name=ee.catering_order
        WHERE ee.docstatus=1 ORDER BY ee.expense_date DESC
    """, as_dict=True)
