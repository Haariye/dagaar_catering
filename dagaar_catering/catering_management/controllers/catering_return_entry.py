# Copyright (c) 2024, DagaarSoft — catering_return_entry.py

import frappe
from frappe.utils import flt


def on_submit(doc, method=None):
	for item in (doc.items or []):
		item.amount = flt(item.qty) * flt(item.valuation_rate)
	doc.total_return_value = sum(flt(i.amount) for i in (doc.items or []))
	_create_stock_entry(doc)


def _create_stock_entry(doc):
	if not doc.warehouse:
		return
	try:
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Receipt"
		se.purpose = "Material Receipt"
		se.posting_date = doc.posting_date
		se.company = doc.company
		se.catering_order = doc.catering_order
		se.remarks = f"Return: {doc.name}"
		for item in (doc.items or []):
			se.append("items", {
				"item_code": item.item_code,
				"qty": flt(item.qty),
				"uom": item.uom,
				"t_warehouse": doc.warehouse,
				"basic_rate": flt(item.valuation_rate),
			})
		se.insert(ignore_permissions=True)
		frappe.db.set_value("Catering Return Entry", doc.name, "stock_entry", se.name)
	except Exception as e:
		frappe.log_error(f"Stock Entry for return {doc.name}: {e}")
