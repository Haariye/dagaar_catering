# Copyright (c) 2024, DagaarSoft — utils/queries.py

import frappe
from frappe.utils import flt


@frappe.whitelist()
def get_menu_package_items(package_name):
	if not package_name:
		return []
	return frappe.db.get_all(
		"Catering Menu Package Item",
		filters={"parent": package_name},
		fields=["item_code", "item_name", "qty_per_guest", "uom", "rate", "item_category"],
		order_by="idx",
	)


@frappe.whitelist()
def get_recipe_cost(recipe_name, qty=1):
	ingredients = frappe.db.get_all(
		"Catering Recipe Ingredient",
		filters={"parent": recipe_name},
		fields=["item_code", "qty", "uom", "cost_per_unit"],
	)
	total = sum(flt(i.qty) * flt(i.cost_per_unit) * flt(qty) for i in ingredients)
	return {"total_cost": round(total, 4), "ingredients": ingredients}
