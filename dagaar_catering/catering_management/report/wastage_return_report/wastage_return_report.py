import frappe
from frappe import _
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("Document"), "fieldname": "doc_name", "fieldtype": "Data", "width": 150},
        {"label": _("Type"), "fieldname": "entry_type", "fieldtype": "Data", "width": 90},
        {"label": _("Order"), "fieldname": "catering_order", "fieldtype": "Link", "options": "Catering Order", "width": 150},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Data", "width": 140},
        {"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
        {"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
        {"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 80},
        {"label": _("Value"), "fieldname": "value", "fieldtype": "Currency", "width": 110},
        {"label": _("Reason"), "fieldname": "reason", "fieldtype": "Data", "width": 180},
    ]
    cw = "we.docstatus=1"; cr = "re.docstatus=1"
    if filters.get("from_date"): cw += " AND we.posting_date >= %(from_date)s"; cr += " AND re.posting_date >= %(from_date)s"
    if filters.get("to_date"): cw += " AND we.posting_date <= %(to_date)s"; cr += " AND re.posting_date <= %(to_date)s"
    wastage = frappe.db.sql(f"SELECT we.name AS doc_name, 'Wastage' AS entry_type, we.catering_order, co.customer, we.posting_date, wi.item_code, wi.qty, wi.amount AS value, wi.reason FROM `tabCatering Wastage Entry` we JOIN `tabCatering Wastage Item` wi ON wi.parent=we.name LEFT JOIN `tabCatering Order` co ON co.name=we.catering_order WHERE {cw}", filters, as_dict=True)
    returns = frappe.db.sql(f"SELECT re.name AS doc_name, 'Return' AS entry_type, re.catering_order, co.customer, re.posting_date, ri.item_code, ri.qty, ri.amount AS value, ri.reason FROM `tabCatering Return Entry` re JOIN `tabCatering Return Item` ri ON ri.parent=re.name LEFT JOIN `tabCatering Order` co ON co.name=re.catering_order WHERE {cr}", filters, as_dict=True)
    data = list(wastage) + list(returns)
    data.sort(key=lambda x: str(x.get("posting_date") or ""), reverse=True)
    return columns, data
