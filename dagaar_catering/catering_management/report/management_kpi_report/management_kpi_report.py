import frappe
from frappe import _
from frappe.utils import flt, today, add_months
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("KPI"), "fieldname": "kpi_name", "fieldtype": "Data", "width": 260},
        {"label": _("Current Period"), "fieldname": "current_value", "fieldtype": "Data", "width": 150},
        {"label": _("Previous Period"), "fieldname": "prev_value", "fieldtype": "Data", "width": 150},
        {"label": _("Change"), "fieldname": "change", "fieldtype": "Data", "width": 130},
        {"label": _("Trend"), "fieldname": "trend", "fieldtype": "Data", "width": 70},
        {"label": _("Target"), "fieldname": "target", "fieldtype": "Data", "width": 110},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
    ]
    fd = filters.get("from_date") or add_months(today(), -1)
    td = filters.get("to_date") or today()
    co = filters.get("company")
    base = f"docstatus=1 AND event_date BETWEEN '{fd}' AND '{td}'"
    if co: base += f" AND company='{co}'"
    pfd = add_months(fd,-1); ptd = add_months(td,-1)
    prev = f"docstatus=1 AND event_date BETWEEN '{pfd}' AND '{ptd}'"
    try: tm = flt(frappe.db.get_single_value("Catering Settings","minimum_margin_percent") or 20)
    except: tm = 20.0
    def q(s):
        try: return frappe.db.sql(s)[0][0] or 0
        except: return 0
    def bkpi(name, cur, prv, fmt="int", target=None, lib=False):
        def fv(v):
            if fmt=="currency": return f"{flt(v):,.2f}"
            elif fmt=="pct": return f"{flt(v):.1f}%"
            else: return str(int(flt(v)))
        chg = flt(cur)-flt(prv); cp = (chg/flt(prv)*100) if flt(prv) else 0
        trend = ("▲" if not lib else "▼") if chg>0 else (("▼" if not lib else "▲") if chg<0 else "—")
        st = ""
        if target is not None: st = "✅ Good" if (flt(cur)<=flt(target) if lib else flt(cur)>=flt(target)) else "⚠️ Low"
        return {"kpi_name": name,"current_value": fv(cur),"prev_value": fv(prv),"change": f"{'+' if chg>0 else ''}{fv(chg)} ({cp:+.1f}%)" if prv else fv(cur),"trend": trend,"target": fv(target) if target is not None else "-","status": st}
    data = []
    data.append(bkpi("Total Catering Orders", q(f"SELECT COUNT(*) FROM `tabCatering Order` WHERE {base}"), q(f"SELECT COUNT(*) FROM `tabCatering Order` WHERE {prev}")))
    cur_rev = q(f"SELECT IFNULL(SUM(total_order_value),0) FROM `tabCatering Order` WHERE {base}")
    prv_rev = q(f"SELECT IFNULL(SUM(total_order_value),0) FROM `tabCatering Order` WHERE {prev}")
    data.append(bkpi("Total Revenue", cur_rev, prv_rev, fmt="currency"))
    cur_mg = q(f"SELECT AVG(cs.gross_margin_percent) FROM `tabCatering Cost Sheet` cs JOIN `tabCatering Order` co ON co.name=cs.catering_order WHERE cs.docstatus=1 AND co.{base}")
    data.append(bkpi("Avg Gross Margin %", cur_mg, 0, fmt="pct", target=tm))
    data.append(bkpi("Outstanding AR", q("SELECT IFNULL(SUM(outstanding_amount),0) FROM `tabSales Invoice` WHERE docstatus=1 AND status IN ('Unpaid','Overdue','Partly Paid')"), 0, fmt="currency"))
    data.append(bkpi("Outstanding AP", q("SELECT IFNULL(SUM(outstanding_amount),0) FROM `tabPurchase Invoice` WHERE docstatus=1 AND status IN ('Unpaid','Overdue','Partly Paid')"), 0, fmt="currency"))
    wv = q(f"SELECT IFNULL(SUM(we.total_wastage_value),0) FROM `tabCatering Wastage Entry` we JOIN `tabCatering Order` co ON co.name=we.catering_order WHERE we.docstatus=1 AND co.{base}")
    wp = (flt(wv)/flt(cur_rev)*100) if flt(cur_rev) else 0
    data.append(bkpi("Wastage % of Revenue", wp, 0, fmt="pct", target=5.0, lib=True))
    data.append(bkpi("Orders Closed", q(f"SELECT COUNT(*) FROM `tabCatering Order` WHERE {base} AND status='Closed'"), 0))
    data.append(bkpi("Emergency Expenses", q(f"SELECT IFNULL(SUM(ee.total_amount),0) FROM `tabCatering Emergency Expense` ee JOIN `tabCatering Order` co ON co.name=ee.catering_order WHERE ee.docstatus=1 AND co.{base}"), 0, fmt="currency"))
    return columns, data
