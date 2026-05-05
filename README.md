# DagaarSoft Catering — Enterprise Catering Management System

**Version 2.0** — Built on ERPNext v15

A workflow-driven, context-aware, fully-integrated catering management system for ERPNext. Manage the complete lifecycle of every catering event — from the first customer call to the financial close — with automatic creation and linking of Quotations, Sales Orders, Sales Invoices, Payment Entries, Material Requests, Work Orders, Stock Entries, and Delivery Notes.

## What Makes This Enterprise-Grade

✅ **Workflow-Driven** — Every action is guided. Buttons appear contextually based on what's been done.

✅ **Context-Aware** — The system knows what comes next. Create Sales Order? Button appears after Quotation. Production Plan? Only after deposit.

✅ **Auto-Fetching** — Customer details auto-fill from Customer master. Currency from Company. Items from Menu Package. BOMs from Items. Raw materials calculated from BOM × guest count.

✅ **Fully Integrated with ERPNext** — Every accounting and inventory document is real ERPNext data. Hits Sales, Buying, Stock, Manufacturing, Accounting modules natively.

✅ **Package-Driven Logic** — Select a Menu Package and items, BOMs, pricing, wastage % all auto-load.

✅ **Real Financial Reporting** — P&L, Margin Analysis, Balance Sheet Impact, Cash Flow — all from real GL data, not parallel records.

✅ **Multi-Company, Multi-Branch, Multi-Currency** — Built for scale.

## The Catering Order Lifecycle

```
Draft → Quoted → Confirmed → Deposit Received → In Production
  → Ready to Deliver → Delivered → Invoiced → Paid → Closed
```

At every stage, the system:
- Validates business rules (margin, deposit, delivery before invoice)
- Creates the next ERPNext document with one click, fully pre-populated
- Updates the Catering Order status automatically
- Logs every action to the audit trail

## Architecture

- **App Name**: `dagaar_catering`
- **Module**: `Dagaar Catering`
- **Required Apps**: `frappe`, `erpnext`
- **DocTypes**: 31
- **Reports**: 21
- **Custom Fields**: `catering_order` Link field added to 11 ERPNext doctypes for reverse linkage

## Key DocTypes

### Master Data
- **Catering Settings** (single) — All defaults: company, branch, accounts, warehouses, business rules
- **Catering Menu Package** — Reusable templates with items, BOMs, pricing, wastage
- **Catering Recipe** — Detailed recipes with ingredients and steps, can generate BOM

### Operational
- **Catering Order** — The control hub. Workflow-driven dashboard with action buttons
- **Catering Cost Sheet** — Per-event cost calculation with margin check
- **Catering Production Plan** — Kitchen task list, integrates with Work Orders
- **Catering Delivery Plan** — Event day delivery + service team
- **Catering Closing Sheet** — Final P&L review before order closure

### Daily Recording
- **Catering Wastage Entry** — Records waste, creates Stock Entry write-off
- **Catering Return Entry** — Records returns, creates Stock Entry receipt
- **Catering Emergency Expense** — Unplanned costs, creates Journal Entry

### Audit
- **Catering Activity Log** — Every action timestamped and attributed

## Roles

11 catering-specific roles: Manager, Sales User, Finance User/Manager, Kitchen User/Manager, Procurement User/Manager, Delivery User, Auditor, Management.

## Installation

See [INSTALL.md](INSTALL.md) for complete instructions.

## License

MIT — Copyright (c) 2024 DagaarSoft
