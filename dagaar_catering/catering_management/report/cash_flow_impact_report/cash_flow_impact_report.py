# Copyright (c) 2024, DagaarSoft
import frappe
from frappe import _
from frappe.utils import flt

def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)

def get_columns():
    return [
        {"label": _("Period"), "fieldname": "period", "fieldtype": "Data", "width": 100},
        {"label": _("Cash In (Receipts)"), "fieldname": "cash_in", "fieldtype": "Currency", "width": 140},
        {"label": _("Deposits Received"), "fieldname": "deposits", "fieldtype": "Currency", "width": 140},
        {"label": _("Cash Out (Payments)"), "fieldname": "cash_out", "fieldtype": "Currency", "width": 150},
        {"label": _("Supplier Payments"), "fieldname": "supplier_payments", "fieldtype": "Currency", "width": 140},
        {"label": _("Emergency Cash Out"), "fieldname": "emergency_cash", "fieldtype": "Currency", "width": 140},
        {"label": _("Net Cash Flow"), "fieldname": "net_cash_flow", "fieldtype": "Currency", "width": 130},
        {"label": _("Outstanding AR"), "fieldname": "outstanding_ar", "fieldtype": "Currency", "width": 130},
        {"label": _("Outstanding AP"), "fieldname": "outstanding_ap", "fieldtype": "Currency", "width": 130},
    ]

def get_data(filters):
    cond = "pe.docstatus=1"
    if filters.get("from_date"): cond += " AND pe.posting_date >= %(from_date)s"
    if filters.get("to_date"): cond += " AND pe.posting_date <= %(to_date)s"
    if filters.get("company"): cond += " AND pe.company = %(company)s"
    periods = frappe.db.sql(f"""
        SELECT DATE_FORMAT(posting_date,'%Y-%m') AS period_key, DATE_FORMAT(posting_date,'%b %Y') AS period
        FROM `tabPayment Entry` WHERE {cond} GROUP BY period_key ORDER BY period_key
    """, filters, as_dict=True)
    data = []
    for p in periods:
        pk = p.period_key
        q = lambda s: (frappe.db.sql(s, pk)[0][0] or 0)
        cash_in = q("SELECT IFNULL(SUM(paid_amount),0) FROM `tabPayment Entry` WHERE docstatus=1 AND payment_type='Receive' AND DATE_FORMAT(posting_date,'%Y-%m')=%s")
        deposits = q("SELECT IFNULL(SUM(paid_amount),0) FROM `tabPayment Entry` WHERE docstatus=1 AND payment_type='Receive' AND (remarks LIKE '%%deposit%%' OR remarks LIKE '%%advance%%') AND DATE_FORMAT(posting_date,'%Y-%m')=%s")
        cash_out = q("SELECT IFNULL(SUM(paid_amount),0) FROM `tabPayment Entry` WHERE docstatus=1 AND payment_type='Pay' AND DATE_FORMAT(posting_date,'%Y-%m')=%s")
        sup_pay = q("SELECT IFNULL(SUM(paid_amount),0) FROM `tabPayment Entry` WHERE docstatus=1 AND payment_type='Pay' AND party_type='Supplier' AND DATE_FORMAT(posting_date,'%Y-%m')=%s")
        emerg = q("SELECT IFNULL(SUM(total_amount),0) FROM `tabCatering Emergency Expense` WHERE docstatus=1 AND DATE_FORMAT(expense_date,'%Y-%m')=%s")
        ar = q("SELECT IFNULL(SUM(outstanding_amount),0) FROM `tabSales Invoice` WHERE docstatus=1 AND status IN ('Unpaid','Partly Paid','Overdue') AND DATE_FORMAT(posting_date,'%Y-%m') <= %s")
        ap = q("SELECT IFNULL(SUM(outstanding_amount),0) FROM `tabPurchase Invoice` WHERE docstatus=1 AND status IN ('Unpaid','Partly Paid','Overdue') AND DATE_FORMAT(posting_date,'%Y-%m') <= %s")
        data.append({"period": p.period,"cash_in": flt(cash_in),"deposits": flt(deposits),"cash_out": flt(cash_out),"supplier_payments": flt(sup_pay),"emergency_cash": flt(emerg),"net_cash_flow": flt(cash_in)-flt(cash_out),"outstanding_ar": flt(ar),"outstanding_ap": flt(ap)})
    return data
