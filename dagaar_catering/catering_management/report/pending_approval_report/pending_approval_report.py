import frappe
from frappe import _
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("DocType"), "fieldname": "doctype", "fieldtype": "Data", "width": 180},
        {"label": _("Document"), "fieldname": "doc_name", "fieldtype": "Data", "width": 160},
        {"label": _("Order"), "fieldname": "catering_order", "fieldtype": "Link", "options": "Catering Order", "width": 150},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Data", "width": 140},
        {"label": _("Created By"), "fieldname": "owner", "fieldtype": "Data", "width": 130},
        {"label": _("Creation"), "fieldname": "creation", "fieldtype": "Datetime", "width": 130},
        {"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Pending Action"), "fieldname": "pending_action", "fieldtype": "Data", "width": 160},
    ]
    data = []
    for r in frappe.db.sql("SELECT co.name AS doc_name, co.name AS catering_order, co.customer, co.owner, co.creation, co.total_order_value AS amount, 'Order Approval' AS pending_action FROM `tabCatering Order` co WHERE co.docstatus=0", as_dict=True):
        r["doctype"] = "Catering Order"; data.append(r)
    for r in frappe.db.sql("SELECT ee.name AS doc_name, ee.catering_order, co.customer, ee.owner, ee.creation, ee.total_amount AS amount, 'Emergency Expense Approval' AS pending_action FROM `tabCatering Emergency Expense` ee LEFT JOIN `tabCatering Order` co ON co.name=ee.catering_order WHERE ee.docstatus=0", as_dict=True):
        r["doctype"] = "Catering Emergency Expense"; data.append(r)
    for r in frappe.db.sql("SELECT cs.name AS doc_name, cs.catering_order, co.customer, cs.owner, cs.creation, cs.total_cost AS amount, 'Cost Sheet Approval' AS pending_action FROM `tabCatering Cost Sheet` cs LEFT JOIN `tabCatering Order` co ON co.name=cs.catering_order WHERE cs.docstatus=0", as_dict=True):
        r["doctype"] = "Catering Cost Sheet"; data.append(r)
    data.sort(key=lambda x: str(x.get("creation") or ""), reverse=True)
    return columns, data
