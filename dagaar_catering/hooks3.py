app_name = "dagaar_catering"
app_title = "DagaarSoft Catering"
app_publisher = "DagaarSoft"
app_description = "Enterprise Catering Management System by DagaarSoft"
app_email = "support@dagaarsoft.com"
app_license = "MIT"
app_version = "1.0.0"
app_icon = "octicon octicon-briefcase"
app_color = "#e74c3c"

# ─── Required Apps ───────────────────────────────────────────────────────────
required_apps = ["frappe", "erpnext"]

# ─── Fixtures ────────────────────────────────────────────────────────────────
# Note: Workflow fixtures are loaded from dagaar_catering/fixtures/*.json automatically
# Workspace is created programmatically in after_install (avoid content field issues)
fixtures = [
    {
        "dt": "Role",
        "filters": [["name", "in", [
            "Catering Manager",
            "Catering Finance User",
            "Catering Finance Manager",
            "Catering Kitchen User",
            "Catering Kitchen Manager",
            "Catering Procurement User",
            "Catering Procurement Manager",
            "Catering Delivery User",
            "Catering Auditor",
            "Catering Management",
        ]]]
    },
]

# ─── Document Events ─────────────────────────────────────────────────────────
doc_events = {
    "Catering Order": {
        "validate":     "dagaar_catering.catering_management.controllers.catering_order.validate",
        "before_save":  "dagaar_catering.catering_management.controllers.catering_order.before_save",
        "after_insert": "dagaar_catering.catering_management.controllers.catering_order.after_insert",
        "on_submit":    "dagaar_catering.catering_management.controllers.catering_order.on_submit",
        "on_cancel":    "dagaar_catering.catering_management.controllers.catering_order.on_cancel",
        "on_update":    "dagaar_catering.catering_management.controllers.catering_order.on_update",
    },
    "Catering Cost Sheet": {
        "validate":  "dagaar_catering.catering_management.controllers.catering_cost_sheet.validate",
    },
    "Catering Production Plan": {
        "validate":  "dagaar_catering.catering_management.controllers.catering_production_plan.validate",
        "on_submit": "dagaar_catering.catering_management.controllers.catering_production_plan.on_submit",
    },
    "Catering Closing Sheet": {
        "validate":  "dagaar_catering.catering_management.controllers.catering_closing_sheet.validate",
        "on_submit": "dagaar_catering.catering_management.controllers.catering_closing_sheet.on_submit",
    },
    "Catering Emergency Expense": {
        "validate":  "dagaar_catering.catering_management.controllers.catering_emergency_expense.validate",
        "on_submit": "dagaar_catering.catering_management.controllers.catering_emergency_expense.on_submit",
    },
    "Catering Wastage Entry": {
        "on_submit": "dagaar_catering.catering_management.controllers.catering_wastage_entry.on_submit",
    },
    "Catering Return Entry": {
        "on_submit": "dagaar_catering.catering_management.controllers.catering_return_entry.on_submit",
    },

    # ── Hook ERPNext document events to update Catering Order automatically ──
    "Quotation": {
        "on_submit": "dagaar_catering.catering_management.controllers.linkers.update_quotation_status",
        "on_cancel": "dagaar_catering.catering_management.controllers.linkers.update_quotation_status",
    },
    "Sales Order": {
        "on_submit": "dagaar_catering.catering_management.controllers.linkers.update_so_status",
        "on_cancel": "dagaar_catering.catering_management.controllers.linkers.update_so_status",
    },
    "Sales Invoice": {
        "on_submit": "dagaar_catering.catering_management.controllers.linkers.update_si_status",
        "on_cancel": "dagaar_catering.catering_management.controllers.linkers.update_si_status",
    },
    "Payment Entry": {
        "on_submit": "dagaar_catering.catering_management.controllers.linkers.update_payment_status",
        "on_cancel": "dagaar_catering.catering_management.controllers.linkers.update_payment_status",
    },
    "Delivery Note": {
        "on_submit": "dagaar_catering.catering_management.controllers.linkers.update_dn_status",
    },
    "Work Order": {
        "on_submit": "dagaar_catering.catering_management.controllers.linkers.update_wo_status",
    },
}

# ─── Scheduled Tasks ─────────────────────────────────────────────────────────
scheduler_events = {
    "daily":   ["dagaar_catering.catering_management.tasks.daily.execute"],
    "weekly":  ["dagaar_catering.catering_management.tasks.weekly.execute"],
    "monthly": ["dagaar_catering.catering_management.tasks.monthly.execute"],
}

# ─── After Install / Migrate ──────────────────────────────────────────────────
after_install = "dagaar_catering.catering_management.setup.install.after_install"
after_migrate = "dagaar_catering.catering_management.setup.install.after_migrate"

# ─── JS / CSS Includes ────────────────────────────────────────────────────────
app_include_css = "/assets/dagaar_catering/css/dagaar_catering.css"
app_include_js  = "/assets/dagaar_catering/js/dagaar_catering.js"

# ─── DocType Client Scripts ───────────────────────────────────────────────────
doctype_js = {
    "Catering Order":           "public/js/catering_order.js",
    "Catering Recipe":          "public/js/catering_recipe.js",
    "Catering Menu Package":    "public/js/catering_menu_package.js",
    "Catering Cost Sheet":      "public/js/catering_cost_sheet.js",
    "Catering Production Plan": "public/js/catering_production_plan.js",
    "Catering Delivery Plan":   "public/js/catering_delivery_plan.js",
}

# ─── DocType List Scripts ─────────────────────────────────────────────────────
doctype_list_js = {
    "Catering Order": "public/js/catering_order_list.js",
}

# ─── Jinja Helpers ────────────────────────────────────────────────────────────
jinja = {
    "methods": [
        "dagaar_catering.catering_management.utils.jinja_helpers.dagaar_fmt_currency",
        "dagaar_catering.catering_management.utils.jinja_helpers.dagaar_margin_badge",
        "dagaar_catering.catering_management.utils.jinja_helpers.dagaar_status_badge",
        "dagaar_catering.catering_management.utils.jinja_helpers.dagaar_guest_breakdown",
    ],
    "filters": [],
}

# ─── Permission Hooks ─────────────────────────────────────────────────────────
has_permission = {
    "Catering Order":         "dagaar_catering.catering_management.controllers.permissions.has_permission",
    "Catering Cost Sheet":    "dagaar_catering.catering_management.controllers.permissions.has_permission_cost",
    "Catering Closing Sheet": "dagaar_catering.catering_management.controllers.permissions.has_permission_closing",
}

# ─── Portal Items ─────────────────────────────────────────────────────────────
portal_menu_items = []
override_whitelisted_methods = {}
