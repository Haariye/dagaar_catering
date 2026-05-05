import frappe
from frappe import _
from frappe.utils import flt
def execute(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 150},
        {"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 180},
        {"label": _("BOM"), "fieldname": "bom_no", "fieldtype": "Link", "options": "BOM", "width": 140},
        {"label": _("Planned Cost/Unit"), "fieldname": "planned_cost", "fieldtype": "Currency", "width": 130},
        {"label": _("Actual Cost/Unit"), "fieldname": "actual_cost", "fieldtype": "Currency", "width": 130},
        {"label": _("Variance"), "fieldname": "variance", "fieldtype": "Currency", "width": 110},
        {"label": _("Variance %"), "fieldname": "variance_pct", "fieldtype": "Percent", "width": 100},
        {"label": _("Qty Produced"), "fieldname": "qty_produced", "fieldtype": "Float", "width": 100},
    ]
    cond = ""
    if filters.get("from_date"): cond += " AND wo.actual_start_date >= %(from_date)s"
    if filters.get("to_date"): cond += " AND wo.actual_start_date <= %(to_date)s"
    rows = frappe.db.sql(f"SELECT wo.production_item AS item_code, wo.item_name, wo.bom_no, wo.qty AS qty_produced, b.total_cost AS planned_cost_total, b.quantity AS bom_qty FROM `tabWork Order` wo LEFT JOIN `tabBOM` b ON b.name=wo.bom_no WHERE wo.docstatus=1 {cond} ORDER BY wo.item_name", filters, as_dict=True)
    data = []
    for r in rows:
        pu = flt(r.planned_cost_total)/flt(r.bom_qty or 1)
        variance = pu; variance_pct = 0
        data.append({"item_code": r.item_code,"item_name": r.item_name,"bom_no": r.bom_no,"planned_cost": round(pu,4),"actual_cost": 0,"variance": round(variance,4),"variance_pct": round(variance_pct,2),"qty_produced": flt(r.qty_produced)})
    return columns, data
