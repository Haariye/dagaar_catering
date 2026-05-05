# Copyright (c) 2024, DagaarSoft — permissions.py

import frappe

KITCHEN_ONLY_ROLES = {"Catering Kitchen User", "Catering Kitchen Manager"}
FINANCE_ROLES = {"Catering Finance User", "Catering Finance Manager", "Catering Auditor",
                 "Catering Manager", "Catering Management"}


def has_permission(doc, ptype="read", user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	if roles.intersection(KITCHEN_ONLY_ROLES) and not roles.intersection(FINANCE_ROLES):
		if ptype in ("write", "submit", "cancel", "amend", "delete"):
			return False
	return None  # default frappe behaviour


def has_permission_cost(doc, ptype="read", user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	# Kitchen users cannot see Cost Sheets
	if KITCHEN_ONLY_ROLES.intersection(roles) and not FINANCE_ROLES.intersection(roles):
		return False
	return None


def has_permission_closing(doc, ptype="read", user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	allowed = {"Catering Manager", "Catering Finance Manager", "Catering Management", "Catering Auditor"}
	if not allowed.intersection(roles):
		return False
	return None
