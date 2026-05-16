// Copyright (c) 2026, DagaarSoft and contributors
// License: MIT
//
// Catering Order — Enterprise Client Script v3.0
// - No approval workflow (replaced by docstatus Submit/Cancel)
// - menu_packages multi-select child table
// - Quick Expense button (QuickBooks-style write check)
// - Operational buttons appear only after Submit
// - Project link displayed in dashboard

frappe.ui.form.on('Catering Order', {
	refresh: function(frm) {
		if (frm.is_new()) {
			if (frm.fields_dict.workflow_dashboard?.$wrapper) {
				frm.fields_dict.workflow_dashboard.$wrapper.html('');
			}
			if (frm.fields_dict.profitability_html?.$wrapper) {
				frm.fields_dict.profitability_html.$wrapper.html(
					'<p style="color:#999;padding:20px;text-align:center;">Profitability dashboard will populate after Save</p>'
				);
			}
			frm.dashboard.clear_headline();
			return;
		}

		_render_workflow_progress(frm);
		_render_profitability_card(frm);
		_setup_action_buttons(frm);
		_setup_status_indicator(frm);
		_setup_bypass_visibility(frm);
		_render_project_link(frm);
	},

	customer: function(frm) {
		if (frm.doc.customer) {
			frappe.db.get_value('Customer', frm.doc.customer,
				['customer_name', 'customer_group', 'territory'], (r) => {
				if (r) {
					frm.set_value('customer_name', r.customer_name);
					frm.set_value('customer_group', r.customer_group);
					if (r.territory) frm.set_value('territory', r.territory);
				}
			});
		}
	},
});

frappe.ui.form.on('Catering Order Menu Package', {
	menu_package: function(frm, cdt, cdn) {
		// When a Menu Package is picked, fetch its default rate and any descriptors,
		// but DON'T auto-load items (that's an explicit button — see _setup_action_buttons)
		const row = locals[cdt][cdn];
		if (!row.menu_package) return;
		frappe.db.get_value('Catering Menu Package', row.menu_package,
			['price_per_guest', 'package_name'], (r) => {
			if (r && r.price_per_guest && row.guest_count) {
				frappe.model.set_value(cdt, cdn, 'subtotal',
					flt(r.price_per_guest) * flt(row.guest_count));
			}
		});
	},
	guest_count: function(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.menu_package) return;
		frappe.db.get_value('Catering Menu Package', row.menu_package,
			'price_per_guest', (r) => {
			if (r && r.price_per_guest) {
				frappe.model.set_value(cdt, cdn, 'subtotal',
					flt(r.price_per_guest) * flt(row.guest_count || 0));
			}
		});
		// Recompute total_guests locally
		let total = 0;
		(frm.doc.menu_packages || []).forEach(p => total += (p.guest_count || 0));
		frm.doc.total_guests = total;
		frm.refresh_field('total_guests');
	},
});

function _reload_items_from_packages(frm) {
	// Called from the explicit "Load Items from Packages" button.
	// Requires doc to be saved first (server needs doc.name).
	if (frm.is_new()) {
		frappe.msgprint(__('Save the order first, then load items from packages.'));
		return;
	}
	if (!(frm.doc.menu_packages && frm.doc.menu_packages.length)) {
		frappe.msgprint(__('Add at least one menu package row first.'));
		return;
	}
	frappe.confirm(
		__('Load items from all selected packages × their guest counts? Existing items in this order will be replaced.'),
		() => {
			const doSave = frm.is_dirty() ? frm.save() : Promise.resolve();
			doSave.then(() => {
				frappe.call({
					method: 'dagaar_catering.catering_management.controllers.catering_order.load_menu_packages_items',
					args: { catering_order: frm.doc.name },
					freeze: true,
					freeze_message: __('Loading items from packages...'),
					callback: () => {
						frm.reload_doc();
						frappe.show_alert({ message: __('Items loaded from packages'), indicator: 'green' }, 3);
					},
				});
			});
		}
	);
}

frappe.ui.form.on('Catering Order Item', {
	item_code: function(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) return;
		// Force-fetch Item details (BOM, rate, UOM, item_name) even when row is
		// freshly created — fetch_from sometimes doesn't fire reliably in table grids
		frappe.db.get_value('Item', row.item_code,
			['item_name', 'stock_uom', 'standard_rate', 'default_bom'], (r) => {
			if (r) {
				frappe.model.set_value(cdt, cdn, 'item_name', r.item_name);
				frappe.model.set_value(cdt, cdn, 'uom', r.stock_uom);
				if (!row.rate) {
					frappe.model.set_value(cdt, cdn, 'rate', r.standard_rate || 0);
				}
				if (r.default_bom) {
					frappe.model.set_value(cdt, cdn, 'bom', r.default_bom);
				}
			}
		});
	},
	qty_per_guest: (frm, cdt, cdn) => _recalc_item(frm, cdt, cdn),
	guest_count: (frm, cdt, cdn) => _recalc_item(frm, cdt, cdn),
	rate: (frm, cdt, cdn) => _recalc_item(frm, cdt, cdn),
});

function _recalc_item(frm, cdt, cdn) {
	const item = locals[cdt][cdn];
	item.total_qty = (item.qty_per_guest || 0) * (item.guest_count || 0);
	item.amount = (item.total_qty || 0) * (item.rate || 0);
	frm.refresh_field('items');
	_recalc_totals(frm);
}

function _recalc_totals(frm) {
	let subtotal = 0;
	(frm.doc.items || []).forEach(it => { subtotal += (it.amount || 0); });
	frm.doc.subtotal = subtotal;
	frm.doc.discount_amount = subtotal * (frm.doc.discount_percent || 0) / 100;
	frm.doc.total_order_value = subtotal - frm.doc.discount_amount + (frm.doc.total_taxes || 0);
	frm.doc.deposit_amount = frm.doc.total_order_value * (frm.doc.deposit_percent || 0) / 100;
	frm.doc.balance_due = frm.doc.total_order_value - (frm.doc.total_paid || 0);
	['subtotal', 'discount_amount', 'total_order_value', 'deposit_amount', 'balance_due']
		.forEach(f => frm.refresh_field(f));
}

// ─── Action Buttons ────────────────────────────────────────────────────────

function _setup_action_buttons(frm) {
	if (frm.is_new()) return;
	const d = frm.doc;

	// 🍽 Load/Refresh Items from Packages — placed under Menu (top-right "...")
	if (d.status !== 'Closed' && d.menu_packages && d.menu_packages.length > 0) {
		const label = d.docstatus === 1
			? __('🍽 Refresh Items from Packages')
			: __('🍽 Load Items from Packages');
		frm.page.add_menu_item(label, () => _reload_items_from_packages(frm));
	}

	// 💵 Quick Expense — under "Create" group
	if (d.docstatus === 1 && d.status !== 'Closed') {
		frm.add_custom_button(__('💵 Quick Expense'),
			() => _show_quick_expense_dialog(frm), __('Create'));
	}

	if (d.docstatus !== 1) return;
	if (d.status === 'Closed') return;

	const has_items = d.items && d.items.length > 0;

	if (!d.sales_order && has_items) {
		frm.add_custom_button(__('Sales Order'),
			() => _show_sales_order_dialog(frm), __('Create'));
	}
	if (d.sales_order && !d.sales_invoice) {
		frm.add_custom_button(__('Sales Invoice'),
			() => _show_sales_invoice_dialog(frm), __('Create'));
	}
	if (d.sales_invoice) {
		const total_paid = parseFloat(d.total_paid) || 0;
		const order_total = parseFloat(d.total_order_value) || 0;
		if (total_paid + 0.01 < order_total || order_total === 0) {
			frm.add_custom_button(__('Payment Entry'),
				() => _show_payment_entry_dialog(frm), __('Create'));
		}
	}
	if (d.sales_invoice && !d.production_plan) {
		frm.add_custom_button(__('Production Plan'),
			() => _action(frm, 'create_production_plan'), __('Create'));
	}
	if (d.sales_invoice && !d.delivery_plan) {
		frm.add_custom_button(__('Delivery Plan'),
			() => _action(frm, 'create_delivery_plan'), __('Create'));
	}
	if (d.sales_invoice && !d.closing_sheet) {
		frm.add_custom_button(__('Closing Sheet'),
			() => _action(frm, 'create_closing_sheet'), __('Create'));
	}

	if (d.requires_rebill) {
		frm.add_custom_button(__('⚠️ Update Invoice'), () => {
			frappe.confirm(
				__('Sync the Sales Invoice with current order changes? Existing payments stay intact.'),
				() => {
					frappe.call({
						method: 'dagaar_catering.catering_management.controllers.catering_order.update_sales_invoice',
						args: { catering_order: frm.doc.name },
						freeze: true, freeze_message: __('Updating invoice...'),
						callback: () => frm.reload_doc(),
					});
				}
			);
		}).addClass('btn-warning');
	}

	frm.add_custom_button(__('Wastage'),
		() => _record_doc(frm, 'Catering Wastage Entry'), __('Record'));
	frm.add_custom_button(__('Return'),
		() => _record_doc(frm, 'Catering Return Entry'), __('Record'));
	frm.add_custom_button(__('Profitability'),
		() => _show_profitability_dialog(frm), __('View'));
	frm.add_custom_button(__('Profitability Report'), () => {
		frappe.set_route('query-report', 'Catering Order Profitability',
			{ catering_order: frm.doc.name });
	}, __('View'));
}

// ─── Quick Expense Dialog ──────────────────────────────────────────────────

function _show_quick_expense_dialog(frm) {
	frappe.call({
		method: 'dagaar_catering.catering_management.controllers.catering_order.get_quick_expense_defaults',
		args: { catering_order: frm.doc.name },
		callback: (r) => {
			if (!r.message) return;
			const d = r.message;

			const dialog = new frappe.ui.Dialog({
				title: __('Quick Expense'),
				size: 'large',
				fields: [
					{
						fieldname: 'header', fieldtype: 'HTML',
						options: `<div style="background:#f4f7fa;padding:10px 14px;border-left:3px solid #3498db;border-radius:4px;margin-bottom:6px;">
							<b>Cash</b> = pay now (from Bank or Cash). <br>
							<b>Bill</b> = record a payable to a Supplier; settle later via Payment Entry.
						</div>`
					},

					// ─── Entry Type (controls everything) ──────────────────────
					{
						fieldname: 'entry_type', fieldtype: 'Select',
						label: __('Entry Type'), reqd: 1,
						options: 'Cash\nBill',
						default: 'Cash',
						onchange: function() { _qe_toggle_fields(dialog); }
					},
					{ fieldname: 'col_top', fieldtype: 'Column Break' },
					{
						fieldname: 'expense_date', fieldtype: 'Date',
						label: __('Date'), reqd: 1, default: d.default_date
					},

					// ─── Payee / Supplier ──────────────────────────────────────
					{
						fieldname: 'sec_who', fieldtype: 'Section Break',
						label: __('Paid To / Owed To')
					},
					{
						fieldname: 'payee', fieldtype: 'Data',
						label: __('Payee'),
						description: __("Person or vendor receiving the cash payment")
					},
					{
						fieldname: 'supplier', fieldtype: 'Link', options: 'Supplier',
						label: __('Supplier'),
						description: __("Supplier owed money — payable will be created"),
						hidden: 1
					},

					// ─── Amount + Reference ────────────────────────────────────
					{ fieldname: 'sec_money', fieldtype: 'Section Break' },
					{
						fieldname: 'amount', fieldtype: 'Currency',
						label: __('Amount'), reqd: 1
					},
					{ fieldname: 'col_money', fieldtype: 'Column Break' },
					{
						fieldname: 'reference_no', fieldtype: 'Data',
						label: __('Check / Bill / Reference No.')
					},

					// ─── Accounts ──────────────────────────────────────────────
					{
						fieldname: 'sec_accounts', fieldtype: 'Section Break',
						label: __('Accounts')
					},
					{
						fieldname: 'paid_from_account', fieldtype: 'Link', options: 'Account',
						label: __('Pay From (Bank or Cash)'),
						default: d.default_paid_from,
						get_query: () => ({
							filters: {
								account_type: ['in', ['Bank', 'Cash']],
								company: d.company,
								is_group: 0,
							}
						})
					},
					{ fieldname: 'col_accounts', fieldtype: 'Column Break' },
					{
						fieldname: 'expense_account', fieldtype: 'Link', options: 'Account',
						label: __('Expense Account'), reqd: 1,
						description: __("Cost Sheet routes this amount automatically: Labor account → Labor Cost field, Delivery account → Delivery Cost, etc."),
						get_query: () => ({
							filters: {
								root_type: 'Expense',
								company: d.company,
								is_group: 0,
							}
						})
					},

					// ─── Memo ──────────────────────────────────────────────────
					{ fieldname: 'sec_memo', fieldtype: 'Section Break' },
					{
						fieldname: 'memo', fieldtype: 'Small Text', label: __('Memo (optional)')
					},
				],
				primary_action_label: __('Record Expense'),
				primary_action: (values) => {
					// Client-side validation before the call
					if (!_qe_validate(dialog, values)) return;

					dialog.hide();
					frappe.call({
						method: 'dagaar_catering.catering_management.controllers.catering_order.create_quick_expense',
						args: {
							catering_order: frm.doc.name,
							entry_type: values.entry_type,
							payee: values.payee || values.supplier || '',
							supplier: values.supplier || null,
							expense_account: values.expense_account,
							paid_from_account: values.paid_from_account || null,
							amount: values.amount,
							expense_date: values.expense_date,
							memo: values.memo,
							reference_no: values.reference_no,
						},
						freeze: true, freeze_message: __('Recording expense...'),
						callback: (r) => {
							if (r.message) {
								frappe.show_alert({
									message: __('Recorded {0}: {1} — JE: {2}',
										[r.message.entry_type, r.message.payee, r.message.journal_entry]),
									indicator: 'green'
								}, 6);
								window.open(`/app/journal-entry/${encodeURIComponent(r.message.journal_entry)}`, '_blank');
								frm.reload_doc();
							}
						}
					});
				}
			});

			dialog.show();
			// Initialize visibility for the default (Cash)
			_qe_toggle_fields(dialog);
		}
	});
}

function _qe_toggle_fields(dialog) {
	const t = dialog.get_value('entry_type') || 'Cash';
	const isCash = (t === 'Cash');

	// Show/hide fields based on type
	dialog.set_df_property('payee', 'hidden', isCash ? 0 : 1);
	dialog.set_df_property('supplier', 'hidden', isCash ? 1 : 0);
	dialog.set_df_property('paid_from_account', 'hidden', isCash ? 0 : 1);

	// Toggle reqd flags (we validate manually in _qe_validate to avoid stale reqd causing issues)
	dialog.set_df_property('payee', 'reqd', isCash ? 1 : 0);
	dialog.set_df_property('supplier', 'reqd', isCash ? 0 : 1);
	dialog.set_df_property('paid_from_account', 'reqd', isCash ? 1 : 0);

	// Clear values from the hidden fields so they don't carry stale data
	if (isCash) {
		dialog.set_value('supplier', '');
	} else {
		dialog.set_value('payee', '');
		dialog.set_value('paid_from_account', '');
	}

	// Refresh field visibility
	dialog.refresh();
}

function _qe_validate(dialog, values) {
	const t = values.entry_type;
	const missing = [];
	if (!values.amount || values.amount <= 0) missing.push(__('Amount'));
	if (!values.expense_account) missing.push(__('Expense Account'));
	if (!values.expense_date) missing.push(__('Date'));

	if (t === 'Cash') {
		if (!values.payee) missing.push(__('Payee'));
		if (!values.paid_from_account) missing.push(__('Pay From (Bank or Cash)'));
	} else if (t === 'Bill') {
		if (!values.supplier) missing.push(__('Supplier'));
	}

	if (missing.length) {
		frappe.msgprint({
			title: __('Missing Fields'),
			indicator: 'orange',
			message: __('Please fill: {0}', [missing.join(', ')])
		});
		return false;
	}
	return true;
}

// ─── Sales Order / Invoice / Payment Dialogs ───────────────────────────────

function _show_sales_order_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __('Create Sales Order'),
		fields: [
			{ fieldname: 'info', fieldtype: 'HTML',
			  options: `<p>Customer: <b>${frm.doc.customer_name || frm.doc.customer}</b><br>
			  Total: <b>${format_currency(frm.doc.total_order_value, frm.doc.currency)}</b></p>` },
			{ fieldname: 'auto_submit', fieldtype: 'Check', label: 'Auto-submit Sales Order', default: 1 },
		],
		primary_action_label: __('Create'),
		primary_action: (values) => {
			dialog.hide();
			frappe.call({
				method: 'dagaar_catering.catering_management.controllers.catering_order.create_sales_order',
				args: { catering_order: frm.doc.name, auto_submit: values.auto_submit ? 1 : 0 },
				freeze: true, freeze_message: __('Creating Sales Order...'),
				callback: (r) => {
					if (r.message) _open_in_new_tab(frm, 'Sales Order', r.message);
				}
			});
		}
	});
	dialog.show();
}

function _show_sales_invoice_dialog(frm) {
	frappe.call({
		method: 'dagaar_catering.catering_management.controllers.catering_order.get_sales_invoice_defaults',
		args: { catering_order: frm.doc.name },
		callback: (r) => {
			if (!r.message) return;
			if (r.message.error) {
				frappe.msgprint({ title: __('Cannot Create'), message: r.message.error, indicator: 'red' });
				return;
			}
			const d = r.message;
			const dialog = new frappe.ui.Dialog({
				title: __('Create Sales Invoice'),
				fields: [
					{ fieldname: 'info', fieldtype: 'HTML',
					  options: `<p>Order Total: <b>${format_currency(d.grand_total, d.currency)}</b></p>` },
					{ fieldname: 'additional_discount', fieldtype: 'Currency',
					  label: 'Additional Discount Amount', default: 0 },
					{ fieldname: 'auto_submit', fieldtype: 'Check',
					  label: 'Auto-submit Invoice', default: 1 },
				],
				primary_action_label: __('Create Invoice'),
				primary_action: (values) => {
					dialog.hide();
					frappe.call({
						method: 'dagaar_catering.catering_management.controllers.catering_order.create_sales_invoice',
						args: {
							catering_order: frm.doc.name,
							additional_discount: values.additional_discount || 0,
							auto_submit: values.auto_submit ? 1 : 0,
						},
						freeze: true, freeze_message: __('Creating Sales Invoice...'),
						callback: (r) => {
							if (r.message) _open_in_new_tab(frm, 'Sales Invoice', r.message);
						}
					});
				}
			});
			dialog.show();
		}
	});
}

function _show_payment_entry_dialog(frm) {
	frappe.call({
		method: 'dagaar_catering.catering_management.controllers.catering_order.get_payment_defaults',
		args: { catering_order: frm.doc.name },
		callback: (r) => {
			if (!r.message) return;
			if (r.message.error) {
				frappe.msgprint({ title: __('Cannot Create'), message: r.message.error, indicator: 'red' });
				return;
			}
			const d = r.message;
			const dialog = new frappe.ui.Dialog({
				title: __('Record Payment'),
				fields: [
					{ fieldname: 'info', fieldtype: 'HTML',
					  options: `<table class="table table-bordered" style="margin:0;">
						<tr><td><b>Sales Invoice</b></td><td>${d.sales_invoice}</td></tr>
						<tr><td><b>Outstanding</b></td><td style="color:#e74c3c;"><b>${format_currency(d.invoice_outstanding, d.currency)}</b></td></tr>
						</table>` },
					{ fieldname: 'paid_amount', fieldtype: 'Currency',
					  label: 'Payment Amount', default: d.suggested_amount, reqd: 1 },
					{ fieldname: 'col1', fieldtype: 'Column Break' },
					{ fieldname: 'mode_of_payment', fieldtype: 'Link', options: 'Mode of Payment',
					  label: 'Mode of Payment', default: d.mode_of_payment, reqd: 1 },
					{ fieldname: 'sb1', fieldtype: 'Section Break' },
					{ fieldname: 'paid_to', fieldtype: 'Link', options: 'Account',
					  label: 'Paid To Account', default: d.paid_to, reqd: 1 },
					{ fieldname: 'col2', fieldtype: 'Column Break' },
					{ fieldname: 'reference_no', fieldtype: 'Data',
					  label: 'Reference No', default: d.reference_no_default },
					{ fieldname: 'reference_date', fieldtype: 'Date',
					  label: 'Reference Date', default: d.reference_date_default },
					{ fieldname: 'sb2', fieldtype: 'Section Break' },
					{ fieldname: 'auto_submit', fieldtype: 'Check',
					  label: 'Auto-submit Payment Entry', default: 1 },
				],
				primary_action_label: __('Record Payment'),
				primary_action: (values) => {
					dialog.hide();
					frappe.call({
						method: 'dagaar_catering.catering_management.controllers.catering_order.create_payment_entry',
						args: {
							catering_order: frm.doc.name,
							paid_amount: values.paid_amount,
							mode_of_payment: values.mode_of_payment,
							paid_to: values.paid_to,
							reference_no: values.reference_no,
							reference_date: values.reference_date,
							auto_submit: values.auto_submit ? 1 : 0,
						},
						freeze: true, freeze_message: __('Recording Payment...'),
						callback: (r) => {
							if (r.message) _open_in_new_tab(frm, 'Payment Entry', r.message);
						}
					});
				}
			});
			dialog.show();
		}
	});
}

function _open_in_new_tab(frm, doctype, docname) {
	frappe.show_alert({
		message: __('Created {0} {1}', [doctype, docname]),
		indicator: 'green'
	}, 5);
	const url = `/app/${frappe.router.slug(doctype)}/${encodeURIComponent(docname)}`;
	window.open(url, '_blank');
	setTimeout(() => frm.reload_doc(), 500);
}

function _action(frm, method) {
	const targetMap = {
		'create_cost_sheet':       { doctype: 'Catering Cost Sheet' },
		'create_production_plan':  { doctype: 'Catering Production Plan' },
		'create_delivery_plan':    { doctype: 'Catering Delivery Plan' },
		'create_closing_sheet':    { doctype: 'Catering Closing Sheet' },
	};
	const target = targetMap[method] || {};
	frappe.call({
		method: `dagaar_catering.catering_management.controllers.catering_order.${method}`,
		args: { catering_order: frm.doc.name },
		freeze: true, freeze_message: __('Processing...'),
		callback: (r) => {
			if (r.message && target.doctype) {
				_open_in_new_tab(frm, target.doctype, r.message);
			} else if (r.message) {
				frm.reload_doc();
			}
		},
	});
}

function _record_doc(frm, doctype) {
	frappe.new_doc(doctype, {
		catering_order: frm.doc.name,
		company: frm.doc.company,
		currency: frm.doc.currency,
		project: frm.doc.project,
	});
}

// ─── Dashboards ────────────────────────────────────────────────────────────

function _render_workflow_progress(frm) {
	if (frm.is_new()) return;
	const stages = [
		{ label: 'Draft',       icon: '📝', done: true },
		{ label: 'Submitted',   icon: '✍️', done: frm.doc.docstatus >= 1 },
		{ label: 'Sales Order', icon: '✅', done: !!frm.doc.sales_order },
		{ label: 'Invoice',     icon: '🧾', done: !!frm.doc.sales_invoice },
		{ label: 'Paid',        icon: '💰', done: frm.doc.status === 'Paid' },
		{ label: 'Production',  icon: '🍳', done: !!frm.doc.production_plan },
		{ label: 'Delivered',   icon: '🚚', done: !!frm.doc.delivery_plan },
		{ label: 'Closed',      icon: '🔒', done: frm.doc.status === 'Closed' },
	];
	const html = `
		<div style="padding:16px 8px;background:linear-gradient(to right,#fef9e7,#eaf2f8);border-radius:8px;">
			<div style="display:flex;justify-content:space-around;align-items:center;flex-wrap:wrap;gap:8px;">
				${stages.map((s, i) => `
					<div style="text-align:center;flex:1;min-width:80px;">
						<div style="width:48px;height:48px;line-height:48px;margin:0 auto 6px;border-radius:50%;
							background:${s.done ? '#27ae60' : '#bdc3c7'};color:white;font-size:22px;font-weight:bold;
							box-shadow:0 2px 4px rgba(0,0,0,0.1);">${s.icon}</div>
						<div style="font-size:11px;font-weight:${s.done ? 'bold' : 'normal'};color:${s.done ? '#1e8449' : '#7f8c8d'};">
							${s.label}
						</div>
					</div>
					${i < stages.length - 1 ? `<div style="flex:0;color:${stages[i+1].done ? '#27ae60' : '#bdc3c7'};font-size:18px;">→</div>` : ''}
				`).join('')}
			</div>
		</div>`;
	frm.fields_dict.workflow_dashboard?.$wrapper.html(html);
}

function _render_profitability_card(frm) {
	if (frm.is_new() || !frm.doc.name) return;
	frappe.call({
		method: 'dagaar_catering.catering_management.controllers.catering_order.get_profitability',
		args: { catering_order: frm.doc.name },
		callback: (r) => {
			if (!r.message) return;
			const d = r.message;
			const cur = d.currency || frm.doc.currency || 'USD';
			const margin_color = d.gross_margin_percent >= 20 ? '#27ae60'
				: d.gross_margin_percent >= 10 ? '#f39c12' : '#e74c3c';
			const html = `
				<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;padding:8px;">
					${_profit_card('Revenue',       d.revenue,    cur, '#3498db', '💰')}
					${_profit_card('Total Cost',    d.total_cost, cur, '#e67e22', '💸')}
					${_profit_card('Gross Profit',  d.gross_profit, cur, '#27ae60', '📈')}
					<div style="background:${margin_color};color:white;padding:16px;border-radius:8px;text-align:center;">
						<div style="font-size:28px;font-weight:bold;">${d.gross_margin_percent}%</div>
						<div style="font-size:12px;opacity:0.9;">Gross Margin</div>
					</div>
					${_profit_card('Invoiced',      d.invoiced,   cur, '#9b59b6', '🧾')}
					${_profit_card('Paid',          d.paid,       cur, '#1abc9c', '✅')}
					${_profit_card('Outstanding',   d.outstanding,cur, '#c0392b', '⚠️')}
					${_profit_card('Wastage',       d.wastage,    cur, '#7f8c8d', '🗑️')}
				</div>`;
			frm.fields_dict.profitability_html?.$wrapper.html(html);
		}
	});
}

function _profit_card(label, value, cur, color, icon) {
	return `
		<div style="background:${color};color:white;padding:16px;border-radius:8px;text-align:center;">
			<div style="font-size:18px;">${icon}</div>
			<div style="font-size:18px;font-weight:bold;margin-top:4px;">${format_currency(value, cur)}</div>
			<div style="font-size:11px;opacity:0.9;margin-top:2px;">${label}</div>
		</div>`;
}

function _show_profitability_dialog(frm) {
	frappe.call({
		method: 'dagaar_catering.catering_management.controllers.catering_order.get_profitability',
		args: { catering_order: frm.doc.name },
		callback: (r) => {
			if (!r.message) return;
			const d = r.message;
			const cur = d.currency;
			new frappe.ui.Dialog({
				title: __('Profitability — {0}', [frm.doc.name]),
				size: 'large',
				fields: [{ fieldtype: 'HTML', fieldname: 'pl', options: `
					<table class="table table-bordered" style="margin:0;">
						<tr style="background:#eaf2f8;font-weight:bold;"><td colspan="2">REVENUE</td></tr>
						<tr><td>Invoiced (net of credit notes)</td><td style="text-align:right;">${format_currency(d.invoiced, cur)}</td></tr>
						<tr><td>Paid</td><td style="text-align:right;color:#27ae60;">${format_currency(d.paid, cur)}</td></tr>
						<tr><td>Outstanding</td><td style="text-align:right;color:#e74c3c;">${format_currency(d.outstanding, cur)}</td></tr>
						<tr style="font-weight:bold;background:#d6eaf8;"><td>Revenue Recognized</td><td style="text-align:right;">${format_currency(d.revenue, cur)}</td></tr>

						<tr style="background:#fef9e7;font-weight:bold;"><td colspan="2">COSTS (live from source data)</td></tr>
						<tr><td>Food</td><td style="text-align:right;">${format_currency(d.food_cost, cur)}</td></tr>
						<tr><td>Beverage</td><td style="text-align:right;">${format_currency(d.beverage_cost, cur)}</td></tr>
						<tr><td>Snacks</td><td style="text-align:right;">${format_currency(d.snacks_cost, cur)}</td></tr>
						<tr><td>Labor (WO + JE)</td><td style="text-align:right;">${format_currency(d.labor_cost, cur)}</td></tr>
						<tr><td>Delivery</td><td style="text-align:right;">${format_currency(d.delivery_cost, cur)}</td></tr>
						<tr><td>Rental / Equipment</td><td style="text-align:right;">${format_currency(d.rental_cost, cur)}</td></tr>
						<tr><td style="padding-left:20px;color:#666;">↳ Wastage (incl. in overhead)</td><td style="text-align:right;color:#999;">${format_currency(d.wastage, cur)}</td></tr>
						<tr><td style="padding-left:20px;color:#666;">↳ Emergency (incl. in overhead)</td><td style="text-align:right;color:#999;">${format_currency(d.emergency, cur)}</td></tr>
						<tr><td>Overhead (incl. wastage + emergency + other JE)</td><td style="text-align:right;">${format_currency(d.overhead_cost, cur)}</td></tr>
						<tr style="font-weight:bold;background:#fadbd8;"><td>Total Cost</td><td style="text-align:right;">${format_currency(d.total_cost, cur)}</td></tr>

						<tr style="background:#eafaf1;font-weight:bold;font-size:16px;"><td>Gross Profit</td><td style="text-align:right;color:${d.gross_profit > 0 ? '#27ae60' : '#e74c3c'};">${format_currency(d.gross_profit, cur)}</td></tr>
						<tr style="background:#eafaf1;font-weight:bold;font-size:16px;"><td>Gross Margin %</td><td style="text-align:right;">${d.gross_margin_percent}%</td></tr>
					</table>
					<p style="margin-top:10px;color:#666;font-size:12px;">
						<i>Computed live from source data — no caching. Includes all submitted Journal Entries tagged to this order, Stock Entries (Manufacture / Material Issue), Work Order labor, Purchase Invoices, Wastage, and Emergency Expenses.</i>
					</p>` }]
			}).show();
		}
	});
}

function _setup_status_indicator(frm) {
	const colors = {
		'Draft': 'gray', 'Confirmed': 'blue', 'Invoiced': 'darkgreen',
		'Paid': 'darkgreen', 'In Production': 'purple',
		'Ready to Deliver': 'yellow', 'Delivered': 'green',
		'Closed': 'darkgray', 'Cancelled': 'red',
	};
	const color = colors[frm.doc.status] || 'gray';

	let docstate_label = '';
	if (frm.doc.docstatus === 0) docstate_label = ' (Draft — click Submit to confirm)';
	else if (frm.doc.docstatus === 1) docstate_label = ' (Submitted)';
	else if (frm.doc.docstatus === 2) docstate_label = ' (Cancelled)';

	frm.dashboard.set_headline_alert(
		`<strong>Status:</strong> ${frm.doc.status || 'Draft'}${docstate_label}`, color
	);

	// Visual hint banner when in Draft, prompting user to Submit
	if (frm.doc.docstatus === 0 && !frm.is_new()) {
		frm.dashboard.add_comment(
			__('This order is in Draft. Click <b>Submit</b> at the top-right to lock it and unlock operational buttons (Sales Order, Sales Invoice, Quick Expense, etc.).'),
			'blue', true
		);
	}
}

function _setup_bypass_visibility(frm) {
	const manager_roles = ['Catering Manager', 'Catering Management', 'System Manager', 'Administrator'];
	const user_roles = frappe.user_roles || [];
	const is_manager = manager_roles.some(r => user_roles.includes(r));
	if (frm.fields_dict.bypass_deposit) {
		frm.toggle_display('bypass_deposit', is_manager);
	}
	if (frm.doc.bypass_deposit) {
		frm.dashboard.add_indicator(__('Deposit Bypassed'), 'orange');
	}
}

function _render_project_link(frm) {
	if (frm.doc.project) {
		const indicator = frm.dashboard.add_indicator(__('Project: {0}', [frm.doc.project]), 'blue');
		if (indicator && indicator.on) {
			indicator.on('click', () => {
				window.open(`/app/project/${encodeURIComponent(frm.doc.project)}`, '_blank');
			});
		}
	}
}
