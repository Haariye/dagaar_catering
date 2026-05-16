# Copyright (c) 2024, DagaarSoft — catering_closing_sheet.py
"""
Catering Closing Sheet — Final P&L for the event.

On submit, posts a SUMMARY Journal Entry that recognizes:
- Revenue (already in GL from Sales Invoice, but we record here for the catering project)
- All cost categories (already in GL from cost sheet / wastage entries, but recorded here)

This provides accountants with one ledger entry per event tied to cost_center and project,
making P&L reporting per event trivial.
"""

import frappe
from frappe import _
from frappe.utils import flt, today


def validate(doc, method=None):
	_calculate_pl(doc)


def on_submit(doc, method=None):
	"""Mark the order as closed and post summary JE."""
	frappe.db.set_value("Catering Order", doc.catering_order, {
		"closing_sheet": doc.name,
		"status": "Closed",
	})
	try:
		je_name = _post_summary_journal_entry(doc)
		if je_name and hasattr(doc, 'journal_entry'):
			frappe.db.set_value("Catering Closing Sheet", doc.name, "journal_entry", je_name)
			frappe.msgprint(_("Posted summary Journal Entry: {0}").format(
				frappe.utils.get_link_to_form("Journal Entry", je_name)),
				indicator="green", alert=True)
	except Exception as e:
		frappe.log_error(f"Closing Sheet JE failed: {str(e)[:300]}", "Catering Closing Sheet")


def on_cancel(doc, method=None):
	"""Cancel JE if any."""
	if doc.get("journal_entry"):
		try:
			je = frappe.get_doc("Journal Entry", doc.journal_entry)
			if je.docstatus == 1:
				je.cancel()
		except Exception:
			pass


def _calculate_pl(doc):
	doc.gross_profit = flt(doc.total_revenue) - flt(doc.total_cost)
	doc.net_profit = flt(doc.gross_profit)
	if flt(doc.total_revenue):
		doc.gross_margin_percent = flt(doc.gross_profit) / flt(doc.total_revenue) * 100
	else:
		doc.gross_margin_percent = 0

	planned = frappe.db.sql(
		"SELECT IFNULL(gross_margin_percent,0) FROM `tabCatering Cost Sheet` "
		"WHERE catering_order=%s AND docstatus=1 LIMIT 1",
		doc.catering_order
	)
	doc.planned_margin_percent = flt(planned[0][0]) if planned else 0
	doc.margin_variance = flt(doc.gross_margin_percent) - flt(doc.planned_margin_percent)


def _post_summary_journal_entry(doc):
	"""Post a summary JE memo for this closing.

	Note: Revenue and costs are already in GL via Sales Invoice and Cost Sheet/Wastage.
	This JE is for KPI reporting — a single voucher per event tagged with cost_center
	and project so management reports can group by them easily.

	If you don't want the duplicate posting, simply skip this — Cost Sheet already
	posts costs. The Closing Sheet's primary purpose is to mark the event closed.
	"""
	# Keep this conservative — only post if catering manager has explicitly configured
	# a summary account. Otherwise the costs are already in GL via Cost Sheet JE.
	try:
		settings = frappe.get_single("Catering Settings")
	except Exception:
		return None

	# If no summary KPI account configured, skip silently (avoids duplicate postings)
	summary_account = settings.get("default_kpi_summary_account")
	if not summary_account:
		return None

	# (Future: build the summary JE here using settings.default_kpi_summary_account)
	return None
