# Copyright (c) 2024, DagaarSoft
import frappe
from frappe import _
from frappe.utils import flt

def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)

def get_columns():
    return [
        {"label": _("Cost Center"), "fieldname": "cost_center", "fieldtype": "Link", "options": "Cost Center", "width": 200},
        {"label": _("Total Income"), "fieldname": "income", "fieldtype": "Currency", "width": 130},
        {"label": _("Total Expense"), "fieldname": "expense", "fieldtype": "Currency", "width": 130},
        {"label": _("COGS"), "fieldname": "cogs", "fieldtype": "Currency", "width": 120},
        {"label": _("Gross Profit"), "fieldname": "gross_profit", "fieldtype": "Currency", "width": 130},
        {"label": _("Margin %"), "fieldname": "margin_pct", "fieldtype": "Percent", "width": 90},
        {"label": _("Catering Orders"), "fieldname": "order_count", "fieldtype": "Int", "width": 110},
    ]

def get_data(filters):
    company = filters.get("company") or frappe.db.get_default("company")
    cond = "gle.company=%(company)s AND gle.is_cancelled=0"
    if filters.get("from_date"): cond += " AND gle.posting_date >= %(from_date)s"
    if filters.get("to_date"): cond += " AND gle.posting_date <= %(to_date)s"
    fp = {"company": company, "from_date": filters.get("from_date"), "to_date": filters.get("to_date")}
    rows = frappe.db.sql(f"""
        SELECT gle.cost_center,
            SUM(CASE WHEN a.root_type='Income' THEN gle.credit-gle.debit ELSE 0 END) AS income,
            SUM(CASE WHEN a.root_type='Expense' THEN gle.debit-gle.credit ELSE 0 END) AS expense
        FROM `tabGL Entry` gle JOIN `tabAccount` a ON a.name=gle.account
        WHERE {cond} AND gle.cost_center IS NOT NULL GROUP BY gle.cost_center ORDER BY income DESC
    """, fp, as_dict=True)
    data = []
    for r in rows:
        if not r.cost_center: continue
        cogs = frappe.db.sql(f"""SELECT IFNULL(SUM(gle.debit-gle.credit),0) FROM `tabGL Entry` gle JOIN `tabAccount` a ON a.name=gle.account WHERE {cond} AND gle.cost_center=%(cc)s AND a.account_type='Cost of Goods Sold'""", dict(**fp, cc=r.cost_center))[0][0]
        oc = frappe.db.count("Catering Order", {"cost_center": r.cost_center, "docstatus": 1})
        inc = flt(r.income); gp = inc - flt(cogs)
        data.append({"cost_center": r.cost_center,"income": inc,"expense": flt(r.expense),"cogs": flt(cogs),"gross_profit": gp,"margin_pct": round((gp/inc*100) if inc else 0,2),"order_count": oc})
    return data
