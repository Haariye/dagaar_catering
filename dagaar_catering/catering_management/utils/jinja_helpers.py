# Copyright (c) 2024, DagaarSoft — Jinja helpers for print formats

import frappe
from frappe.utils import flt, fmt_money


def dagaar_fmt_currency(amount, currency=None):
	if not currency:
		currency = frappe.db.get_default("currency") or "USD"
	return fmt_money(flt(amount), currency=currency)


def dagaar_margin_badge(margin_percent):
	m = flt(margin_percent)
	if m >= 25:   color, label = "#27ae60", "Excellent"
	elif m >= 15: color, label = "#f39c12", "Good"
	elif m >= 5:  color, label = "#e67e22", "Low"
	else:         color, label = "#e74c3c", "Critical"
	return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px">{label} ({m:.1f}%)</span>'


def dagaar_status_badge(status):
	colors = {
		"Draft": "#95a5a6", "Pending Approval": "#f39c12", "Confirmed": "#3498db",
		"Sales Order Created": "#9b59b6", "Deposit Received": "#8e44ad",
		"In Production": "#e67e22", "Ready for Delivery": "#1abc9c",
		"Delivered": "#27ae60", "Invoiced": "#2980b9", "Closed": "#27ae60", "Cancelled": "#e74c3c",
	}
	color = colors.get(status, "#95a5a6")
	return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px">{status}</span>'


def dagaar_guest_breakdown(order_name):
	rows = frappe.db.get_all(
		"Catering Order Guest Type", filters={"parent": order_name},
		fields=["guest_type", "guest_count", "menu_package"], order_by="idx")
	if not rows:
		return ""
	html = "<table style='width:100%;border-collapse:collapse'><tr><th>Type</th><th>Count</th><th>Package</th></tr>"
	for r in rows:
		html += f"<tr><td>{r.guest_type}</td><td>{r.guest_count}</td><td>{r.menu_package or '—'}</td></tr>"
	return html + "</table>"
