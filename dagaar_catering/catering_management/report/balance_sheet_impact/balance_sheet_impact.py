# Copyright (c) 2024, DagaarSoft
import frappe
from frappe import _
from frappe.utils import flt

def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)

def get_columns():
    return [
        {"label": _("Account Head"), "fieldname": "account", "fieldtype": "Data", "width": 220},
        {"label": _("Account Type"), "fieldname": "account_type", "fieldtype": "Data", "width": 140},
        {"label": _("Debit"), "fieldname": "debit", "fieldtype": "Currency", "width": 130},
        {"label": _("Credit"), "fieldname": "credit", "fieldtype": "Currency", "width": 130},
        {"label": _("Balance"), "fieldname": "balance", "fieldtype": "Currency", "width": 130},
        {"label": _("Impact"), "fieldname": "impact_note", "fieldtype": "Data", "width": 220},
    ]

def get_data(filters):
    company = filters.get("company") or frappe.db.get_default("company")
    fd = filters.get("from_date"); td = filters.get("to_date")
    cond = "gle.company = %(company)s AND gle.is_cancelled = 0"
    if fd: cond += " AND gle.posting_date >= %(from_date)s"
    if td: cond += " AND gle.posting_date <= %(to_date)s"
    fp = {"company": company, "from_date": fd, "to_date": td}
    groups = [
        ("Accounts Receivable","Receivable","Customer receivables from catering invoices"),
        ("Stock","Stock","Inventory consumed for catering production"),
        ("Accounts Payable","Payable","Supplier payables for catering purchases"),
        ("Bank","Bank","Cash/bank movement from collections and payments"),
        ("Income","Income","Catering service revenue"),
        ("Cost of Goods Sold","Expense","Direct food and beverage cost"),
        ("Expenses","Expense","Operating expenses including wastage"),
    ]
    data = []
    for name, atype, note in groups:
        row = frappe.db.sql(f"""
            SELECT IFNULL(SUM(gle.debit),0) AS debit, IFNULL(SUM(gle.credit),0) AS credit
            FROM `tabGL Entry` gle JOIN `tabAccount` a ON a.name=gle.account
            WHERE {cond} AND (a.account_type=%(at)s OR a.root_type=%(at)s)
        """, dict(**fp, at=atype), as_dict=True)[0]
        d, c = flt(row.debit), flt(row.credit)
        data.append({"account": name,"account_type": atype,"debit": d,"credit": c,"balance": d-c,"impact_note": note})
    return data
