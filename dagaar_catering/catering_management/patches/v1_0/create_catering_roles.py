import frappe

def execute():
    from dagaar_catering.setup.install import create_catering_roles
    create_catering_roles()
    frappe.db.commit()
