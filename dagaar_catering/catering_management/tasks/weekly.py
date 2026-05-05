# Copyright (c) 2024, DagaarSoft — Weekly tasks

import frappe
from frappe import _
from frappe.utils import today, add_days


def execute():
	send_weekly_summary()


def send_weekly_summary():
	import datetime
	end = datetime.date.today()
	start = end - datetime.timedelta(days=7)

	stats = frappe.db.sql("""
		SELECT COUNT(*) AS orders, IFNULL(SUM(total_order_value),0) AS revenue,
			SUM(CASE WHEN status='Closed' THEN 1 ELSE 0 END) AS closed
		FROM `tabCatering Order`
		WHERE docstatus=1 AND creation BETWEEN %s AND %s
	""", (str(start), str(end)), as_dict=True)
	r = stats[0] if stats else {}

	msg = _("""<h3>DagaarSoft Catering — Weekly Summary</h3>
		<table border="1" cellpadding="6"><tr><td>New Orders</td><td>{orders}</td></tr>
		<tr><td>Closed Orders</td><td>{closed}</td></tr>
		<tr><td>Revenue</td><td>{rev}</td></tr></table>""").format(
		orders=r.get("orders",0), closed=r.get("closed",0), rev=r.get("revenue",0))

	recipients = _get_role_users("Catering Management") + _get_role_users("Catering Manager")
	if recipients:
		frappe.sendmail(recipients=list(set(recipients)),
			subject=_("Catering Weekly Summary — {0}").format(end),
			message=msg, now=False)


def _get_role_users(role):
	rows = frappe.db.sql("""
		SELECT DISTINCT u.email FROM `tabUser` u
		JOIN `tabHas Role` hr ON hr.parent=u.name WHERE hr.role=%s AND u.enabled=1
	""", role, as_dict=True)
	return [r.email for r in rows if r.email]
