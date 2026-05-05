# Installation Guide — DagaarSoft Catering v2.0

## Prerequisites

- ERPNext v15 already installed
- Bench command line access on your server
- A site where you want to install (e.g., `uat.dagaartech.com`)

## Fresh Installation

```bash
# 1. SSH into your server, go to your bench directory
cd /home/frappe/frappe-bench

# 2. Get the app from your source
bench get-app /path/to/dagaar_catering   # if local zip
# OR
bench get-app https://github.com/your-org/dagaar_catering

# 3. Install on the site
bench --site uat.dagaartech.com install-app dagaar_catering

# 4. Run migrate to ensure all DocTypes, Workflows, and Custom Fields are created
bench --site uat.dagaartech.com migrate

# 5. Build assets (CSS/JS)
bench build --app dagaar_catering

# 6. Restart bench
bench restart
```

## Upgrading from v1.0 to v2.0

If you already have v1.0 installed:

```bash
# 1. Backup your site (CRITICAL!)
bench --site uat.dagaartech.com backup --with-files

# 2. Pull/copy the new version over your existing app folder
# (Replace contents of apps/dagaar_catering with the new version)

# 3. Run migrate to apply schema changes
bench --site uat.dagaartech.com migrate

# 4. Run patches manually if needed
bench --site uat.dagaartech.com execute dagaar_catering.dagaar_catering.dagaar_catering.patches.v1_0.add_catering_order_links.execute

# 5. Build and restart
bench build --app dagaar_catering
bench restart
```

## Post-Install Configuration

### Step 1: Configure Catering Settings

Navigate to: **Dagaar Catering → Settings → Catering Settings**

Set these required values:

| Field | Recommendation |
|-------|----------------|
| Default Company | Your main company |
| Default Currency | Match your company currency |
| Default Income Account | Sales account |
| Default Receivable Account | Debtors |
| Default Source Warehouse | Where raw materials live |
| Default WIP Warehouse | Where work-in-progress sits |
| Default FG Warehouse | Where finished goods go |
| Default Wastage Warehouse | Wastage write-off warehouse |
| Default Sales Tax Template | Your sales tax setup |
| Minimum Margin % | 15 (or your minimum acceptable) |
| Default Deposit % | 30 |
| Require SO Before Production | ✓ |
| Require Deposit Before Production | ✓ |
| Require Delivery Before Invoice | ✓ |
| Require Invoice Before Closure | ✓ |

### Step 2: Assign Roles to Users

Go to **Users → [select user] → Roles**. Assign one or more catering roles based on their function.

### Step 3: Create Master Data

1. **Customers** — Use ERPNext's standard Customer module. Set primary contact (auto-fetched into Catering Order).
2. **Items** — Create catering items in the standard Item module. Set Default UOM, Item Group, Maintain Stock if applicable.
3. **BOMs** — For manufactured items (cooked dishes), create BOMs in ERPNext Manufacturing.
4. **Catering Recipes** (optional) — Create recipes that link to items and can generate BOMs.
5. **Catering Menu Packages** — Create your reusable package templates: items + qty per guest + price per guest.

### Step 4: Test the Workflow

1. Create a test Catering Order
2. Select a Menu Package — items should auto-load
3. Save → Click **Create > Quotation**
4. Click **Create > Sales Order**
5. Click **Create > Deposit Payment** → Submit it
6. Click **Create > Production Plan**
7. Click **Create > Material Request**
8. Click **Create > Delivery Plan**
9. Mark Delivery Plan as Delivered
10. Click **Create > Sales Invoice**
11. Receive payment via standard Payment Entry
12. Click **Create > Closing Sheet** → Review → Mark as Approved
13. Click **🔒 Close Order**

## Troubleshooting

### "Module dagaar_catering not found"
Run: `bench --site [site] migrate`

### "Catering Order custom field not appearing on Sales Order"
Run the patch manually:
```bash
bench --site [site] execute dagaar_catering.dagaar_catering.dagaar_catering.setup.install.add_catering_order_custom_fields
```

### "Workflow state field error"
Ensure the workflow JSONs have `"workflow_state_field": "workflow_state"`. Already in v2.0.

### "Permission denied creating documents"
Check user has the Catering Manager or Catering Sales User role.

## Support

For questions: support@dagaarsoft.com
