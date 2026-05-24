# Run in: bench --site uat.dagaartech.com console
exec("""
import frappe
CO = 'COR-2026-00061'
print('\\n===== TIMELINE: ' + CO + ' =====\\n')

# Show creation order of everything
events = []
for dt in ['Sales Invoice', 'Delivery Note', 'Stock Entry', 'Work Order',
           'Catering Delivery Plan', 'Catering Production Plan']:
    try:
        rows = frappe.db.sql(f\"\"\"
            SELECT name, creation, docstatus
            FROM `tab{dt}`
            WHERE catering_order = %s
        \"\"\", CO, as_dict=True)
        for r in rows:
            events.append({'doctype': dt, 'name': r.name,
                          'time': r.creation, 'docstatus': r.docstatus})
    except Exception:
        pass

events.sort(key=lambda x: x['time'])
print('Creation order:')
for e in events:
    print(f'  {e[\"time\"]}  {e[\"doctype\"]:25} {e[\"name\"]}  doc={e[\"docstatus\"]}')

# Check the suspicious DN
print()
print('Delivery Note MAT-DN-2026-00010 details:')
dn = frappe.get_doc('Delivery Note', 'MAT-DN-2026-00010')
print(f'  creation: {dn.creation}')
print(f'  posting_date: {dn.posting_date}')
for item in dn.items:
    print(f'  item={item.item_code} qty={item.qty}')
    print(f'    against_sales_invoice={item.against_sales_invoice}')
    print(f'    si_detail={item.si_detail}')

# Check the supplementary SI was created when?
print()
print('Supplementary SI ACC-SINV-2026-00656:')
si = frappe.get_doc('Sales Invoice', 'ACC-SINV-2026-00656')
print(f'  creation: {si.creation}')
print(f'  posting_date: {si.posting_date}')
print(f'  is_return: {si.is_return}')

print('\\n===== END =====\\n')
""")
