# Copyright (c) 2024, DagaarSoft — Monthly tasks

import frappe
from frappe import _


def execute():
	send_monthly_profitability()


def send_monthly_profitability():
	import datetime, calendar
	today = datetime.date.today()
	if today.month == 1:
		y, m = today.year - 1, 12
	else:
		y, m = today.year, today.month - 1
	start = datetime.date(y, m, 1)
	end = datetime.date(y, m, calendar.monthrange(y, m)[1])

	stats = frappe.db.sql("""
		SELECT COUNT(*) AS orders, IFNULL(SUM(co.total_order_value),0) AS revenue,
			IFNULL(AVG(cs.gross_margin_percent),0) AS avg_margin
		FROM `tabCatering Order` co
		LEFT JOIN `tabCatering Cost Sheet` cs ON cs.catering_order=co.name AND cs.docstatus=1
		WHERE co.docstatus=1 AND co.event_date BETWEEN %s AND %s
	""", (str(start), str(end)), as_dict=True)
	r = stats[0] if stats else {}

	wastage = frappe.db.sql("""
		SELECT IFNULL(SUM(we.total_wastage_value),0) AS w
		FROM `tabCatering Wastage Entry` we
		WHERE we.docstatus=1 AND we.posting_date BETWEEN %s AND %s
	""", (str(start), str(end)), as_dict=True)
	w = (wastage[0].get("w") or 0) if wastage else 0

	msg = _("""<h3>DagaarSoft Catering — Monthly Summary: {month}</h3>
		<table border="1" cellpadding="6">
		<tr><td>Total Orders</td><td>{orders}</td></tr>
		<tr><td>Total Revenue</td><td>{rev}</td></tr>
		<tr><td>Avg Gross Margin</td><td>{margin:.1f}%</td></tr>
		<tr><td>Total Wastage</td><td>{wastage}</td></tr>
		</table>""").format(
		month=start.strftime("%B %Y"),
		orders=r.get("orders",0), rev=r.get("revenue",0),
		margin=r.get("avg_margin",0), wastage=w)

	recipients = _get_role_users("Catering Management") + _get_role_users("Catering Finance Manager")
	if recipients:
		frappe.sendmail(recipients=list(set(recipients)),
			subject=_("Catering Monthly Summary — {0}").format(start.strftime("%B %Y")),
			message=msg, now=False)


def _get_role_users(role):
	rows = frappe.db.sql("""
		SELECT DISTINCT u.email FROM `tabUser` u
		JOIN `tabHas Role` hr ON hr.parent=u.name WHERE hr.role=%s AND u.enabled=1
	""", role, as_dict=True)
	return [r.email for r in rows if r.email]
