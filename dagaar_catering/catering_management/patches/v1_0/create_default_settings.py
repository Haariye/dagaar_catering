import frappe

def execute():
    from dagaar_catering.setup.install import create_catering_roles, create_default_settings
    create_catering_roles()
    create_default_settings()
    frappe.db.commit()
