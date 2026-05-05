import frappe
from frappe import _
from frappe.utils import flt
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("Order"), "fieldname": "order_name", "fieldtype": "Link", "options": "Catering Order", "width": 150},
        {"label": _("Event Date"), "fieldname": "event_date", "fieldtype": "Date", "width": 100},
        {"label": _("Guests"), "fieldname": "total_guests", "fieldtype": "Int", "width": 80},
        {"label": _("Work Orders"), "fieldname": "work_orders", "fieldtype": "Int", "width": 90},
        {"label": _("Completed WO"), "fieldname": "completed_wo", "fieldtype": "Int", "width": 100},
        {"label": _("Wastage Value"), "fieldname": "wastage_value", "fieldtype": "Currency", "width": 120},
        {"label": _("Wastage %"), "fieldname": "wastage_pct", "fieldtype": "Percent", "width": 90},
        {"label": _("Planned Cost"), "fieldname": "planned_cost", "fieldtype": "Currency", "width": 120},
    ]
    cond = "co.docstatus=1"
    if filters.get("from_date"): cond += " AND co.event_date >= %(from_date)s"
    if filters.get("to_date"): cond += " AND co.event_date <= %(to_date)s"
    orders = frappe.db.sql(f"SELECT co.name AS order_name, co.event_date, co.total_guests FROM `tabCatering Order` co WHERE {cond} ORDER BY co.event_date DESC", filters, as_dict=True)
    data = []
    for o in orders:
        wt = frappe.db.count("Work Order",{"catering_order": o.order_name,"docstatus": ["!=",2]})
        wd = frappe.db.count("Work Order",{"catering_order": o.order_name,"status": "Completed"})
        wv = frappe.db.sql("SELECT IFNULL(SUM(total_wastage_value),0) FROM `tabCatering Wastage Entry` WHERE docstatus=1 AND catering_order=%s", o.order_name)[0][0]
        pc = frappe.db.get_value("Catering Cost Sheet",{"catering_order": o.order_name,"docstatus": 1},"total_cost") or 0
        wp = (flt(wv)/flt(pc)*100) if flt(pc) else 0
        data.append({"order_name": o.order_name,"event_date": o.event_date,"total_guests": o.total_guests,"work_orders": wt,"completed_wo": wd,"wastage_value": flt(wv),"wastage_pct": round(wp,2),"planned_cost": flt(pc)})
    return columns, data
