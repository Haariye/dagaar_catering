// Copyright (c) 2024, DagaarSoft and contributors
// License: MIT
//
// Catering Order — Enterprise Client Script v2.1
// Workflow: Sales Order → Sales Invoice → Payment(s) → Production → Closing
// Smart popup dialogs for SO, SI, Payment Entry creation.

frappe.ui.form.on('Catering Order', {

	refresh: function(frm) {
		// On new form, clear any stale visual state from previous order
		if (frm.is_new()) {
			if (frm.fields_dict.workflow_dashboard && frm.fields_dict.workflow_dashboard.$wrapper) {
				frm.fields_dict.workflow_dashboard.$wrapper.html('');
			}
			if (frm.fields_dict.profitability_html && frm.fields_dict.profitability_html.$wrapper) {
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

	menu_package: function(frm) {
		if (frm.doc.menu_package && frm.doc.docstatus === 0) {
			frappe.confirm(
				__('Load items from selected Menu Package?'),
				() => {
					frappe.call({
						method: 'frappe.client.get',
						args: { doctype: 'Catering Menu Package', name: frm.doc.menu_package },
						callback: (r) => {
							if (r.message) _load_package_items(frm, r.message);
						}
					});
				}
			);
		}
	},

	total_guests: function(frm) {
		(frm.doc.items || []).forEach((item) => {
			frappe.model.set_value(item.doctype, item.name, 'guest_count', frm.doc.total_guests);
		});
	},
});

frappe.ui.form.on('Catering Order Item', {
	qty_per_guest: (frm, cdt, cdn) => _recalc_item(frm, cdt, cdn),
	guest_count: (frm, cdt, cdn) => _recalc_item(frm, cdt, cdn),
	rate: (frm, cdt, cdn) => _recalc_item(frm, cdt, cdn),
});

// ════════════════════════════════════════════════════════════════════════════
// ACTION BUTTONS — workflow-driven visibility
// ════════════════════════════════════════════════════════════════════════════

function _setup_action_buttons(frm) {
	if (frm.is_new()) return;
	if (frm.doc.docstatus === 2) return;
	if (frm.doc.status === 'Closed') return;

	const d = frm.doc;
	const has_items = d.items && d.items.length > 0;

	// 1. Create Sales Order — show until SO exists
	if (!d.sales_order && has_items) {
		frm.add_custom_button(__('Sales Order'), () => _show_sales_order_dialog(frm), __('Create'));
	}

	// 2. Create Sales Invoice — show after SO exists, until SI exists
	if (d.sales_order && !d.sales_invoice) {
		frm.add_custom_button(__('Sales Invoice'), () => _show_sales_invoice_dialog(frm), __('Create'));
	}

	// 3. Create Payment Entry — show when SI exists AND not fully paid
	// Use Catering Order's own total_paid vs total_order_value (kept in sync by linker)
	// This avoids async calls that cause button-flicker.
	if (d.sales_invoice) {
		const total_paid = parseFloat(d.total_paid) || 0;
		const order_total = parseFloat(d.total_order_value) || 0;
		// Show if not fully paid (allow some float rounding tolerance)
		if (total_paid + 0.01 < order_total || order_total === 0) {
			frm.add_custom_button(__('Payment Entry'),
				() => _show_payment_entry_dialog(frm), __('Create'));
		}
	}

	// 4. Create Production Plan — show when SI exists AND no plan yet
	if (d.sales_invoice && !d.production_plan) {
		frm.add_custom_button(__('Production Plan'),
			() => _action(frm, 'create_production_plan'), __('Create'));
	}

	// 5. Create Material Request — show when no MR yet
	if (!d.material_request) {
		frm.add_custom_button(__('Material Request'),
			() => _action(frm, 'create_material_request'), __('Create'));
	}

	// 6. Create Cost Sheet — show until exists
	if (!d.cost_sheet) {
		frm.add_custom_button(__('Cost Sheet'),
			() => _action(frm, 'create_cost_sheet'), __('Create'));
	}

	// 7. Create Delivery Plan — show when SI exists AND no DP yet
	if (d.sales_invoice && !d.delivery_plan) {
		frm.add_custom_button(__('Delivery Plan'),
			() => _action(frm, 'create_delivery_plan'), __('Create'));
	}

	// 8. Create Closing Sheet — show when SI exists AND no closing yet
	if (d.sales_invoice && !d.closing_sheet) {
		frm.add_custom_button(__('Closing Sheet'),
			() => _action(frm, 'create_closing_sheet'), __('Create'));
	}

	// 9. Update Invoice — show when order changed after billing
	if (d.requires_rebill) {
		frm.add_custom_button(__('⚠️ Update Invoice'), () => {
			frappe.confirm(
				__('This will sync the Sales Invoice with the current order. If the invoice is still Draft, it will be edited in place. If it is Submitted, a supplementary invoice (or credit note) will be created for the difference. Sales Order and Work Orders are NOT cancelled. Continue?'),
				() => {
					frappe.call({
						method: 'dagaar_catering.catering_management.controllers.catering_order.update_sales_invoice',
						args: { catering_order: frm.doc.name },
						freeze: true,
						freeze_message: __('Computing differences and updating invoice...'),
						callback: (r) => {
							if (r.message) {
								let msg = '';
								if (r.message.action === 'updated_draft') {
									msg = __('Draft invoice updated: {0}', [r.message.sales_invoice]);
								} else if (r.message.action === 'supplementary') {
									msg = __('Created supplementary docs: {0}', [r.message.documents.join(', ')]);
								} else if (r.message.action === 'no_change') {
									msg = __('No billing changes detected.');
								}
								frappe.show_alert({ message: msg, indicator: 'green' }, 8);
								frm.reload_doc();
							}
						}
					});
				}
			);
		}).addClass('btn-warning');
	}

	// Record buttons (always available)
	frm.add_custom_button(__('Wastage'),
		() => _record_doc(frm, 'Catering Wastage Entry'), __('Record'));
	frm.add_custom_button(__('Return'),
		() => _record_doc(frm, 'Catering Return Entry'), __('Record'));
	frm.add_custom_button(__('Emergency Expense'),
		() => _record_doc(frm, 'Catering Emergency Expense'), __('Record'));

	// View buttons
	frm.add_custom_button(__('Profitability'),
		() => _show_profitability_dialog(frm), __('View'));

	// Top-level Close Order (only when closing sheet exists and ready)
	if (d.closing_sheet && d.status !== 'Closed') {
		frm.add_custom_button(__('🔒 Close Order'), () => {
			frappe.confirm(__('Close this Catering Order?'),
				() => _action(frm, 'close_catering_order'));
		}).addClass('btn-danger');
	}
}


// ════════════════════════════════════════════════════════════════════════════
// DIALOGS
// ════════════════════════════════════════════════════════════════════════════

function _show_sales_order_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __('Create Sales Order'),
		fields: [
			{ fieldname: 'info', fieldtype: 'HTML',
			  options: `<p>This will create a Sales Order for <b>${frm.doc.customer_name || frm.doc.customer}</b><br>
			  Total: <b>${format_currency(frm.doc.total_order_value, frm.doc.currency)}</b></p>` },
			{ fieldname: 'auto_submit', fieldtype: 'Check', label: 'Auto-submit Sales Order', default: 1 },
		],
		primary_action_label: __('Create'),
		primary_action: (values) => {
			dialog.hide();
			frappe.call({
				method: 'dagaar_catering.catering_management.controllers.catering_order.create_sales_order',
				args: {
					catering_order: frm.doc.name,
					auto_submit: values.auto_submit ? 1 : 0,
				},
				freeze: true, freeze_message: __('Creating Sales Order...'),
				callback: (r) => {
					if (r.message) {
						_open_print_or_redirect(frm, 'Sales Order', r.message, values.auto_submit);
					}
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
					{ fieldname: 'customer_info', fieldtype: 'HTML',
					  options: `<p>Customer: <b>${d.customer_name || d.customer}</b><br>
					  Order Total: <b>${format_currency(d.grand_total, d.currency)}</b></p>` },
					{ fieldname: 'additional_discount', fieldtype: 'Currency',
					  label: 'Additional Discount Amount',
					  description: 'Optional flat discount on grand total',
					  default: 0 },
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
							if (r.message) {
								_open_print_or_redirect(frm, 'Sales Invoice', r.message, values.auto_submit);
							}
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
					{ fieldname: 'invoice_info', fieldtype: 'HTML',
					  options: `
						<table class="table table-bordered" style="margin:0;">
						<tr><td><b>Sales Invoice</b></td><td>${d.sales_invoice}</td></tr>
						<tr><td><b>Invoice Total</b></td><td>${format_currency(d.invoice_grand_total, d.currency)}</td></tr>
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
					if (values.paid_amount > d.invoice_outstanding) {
						frappe.msgprint({
							title: __('Amount Too High'),
							message: __('Payment amount cannot exceed outstanding amount {0}',
								[format_currency(d.invoice_outstanding, d.currency)]),
							indicator: 'red'
						});
						return;
					}
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
							if (r.message) {
								_open_print_or_redirect(frm, 'Payment Entry', r.message, values.auto_submit);
							}
						}
					});
				}
			});
			dialog.show();
		}
	});
}

function _open_print_or_redirect(frm, doctype, docname, is_submitted) {
	frappe.show_alert({
		message: __('Created {0} {1} — opened in new tab', [doctype, docname]),
		indicator: 'green'
	}, 5);

	// Always open the created document in a NEW TAB (no auto-print)
	const url = `/app/${frappe.router.slug(doctype)}/${encodeURIComponent(docname)}`;
	window.open(url, '_blank');

	// Refresh the Catering Order so linked field shows the new doc
	setTimeout(() => frm.reload_doc(), 500);
}

// ════════════════════════════════════════════════════════════════════════════
// GENERIC ACTION (for non-popup actions)
// ════════════════════════════════════════════════════════════════════════════

function _action(frm, method) {
	const targetMap = {
		'create_cost_sheet':       { doctype: 'Catering Cost Sheet',       },
		'create_production_plan':  { doctype: 'Catering Production Plan',  },
		'create_material_request': { doctype: 'Material Request',          },
		'create_delivery_plan':    { doctype: 'Catering Delivery Plan',    },
		'create_closing_sheet':    { doctype: 'Catering Closing Sheet',    },
		'close_catering_order':    { doctype: null,                        },
	};
	const target = targetMap[method] || {};

	frappe.call({
		method: `dagaar_catering.catering_management.controllers.catering_order.${method}`,
		args: { catering_order: frm.doc.name },
		freeze: true,
		freeze_message: __('Processing...'),
		callback: (r) => {
			if (r.message && target.doctype) {
				frappe.show_alert({
					message: __('Created {0} {1} — opened in new tab', [target.doctype, r.message]),
					indicator: 'green'
				}, 5);
				// Open in new tab instead of navigating away
				const url = `/app/${frappe.router.slug(target.doctype)}/${encodeURIComponent(r.message)}`;
				window.open(url, '_blank');
				setTimeout(() => frm.reload_doc(), 500);
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
		currency: frm.doc.currency
	});
}

// ════════════════════════════════════════════════════════════════════════════
// WORKFLOW PROGRESS DASHBOARD
// ════════════════════════════════════════════════════════════════════════════

function _render_workflow_progress(frm) {
	if (frm.is_new()) return;
	const stages = [
		{ key: 'Draft',            label: 'Draft',     icon: '📝', done: true },
		{ key: 'Confirmed',        label: 'Sales Order',   icon: '✅', done: !!frm.doc.sales_order },
		{ key: 'Invoiced',         label: 'Invoice',   icon: '🧾', done: !!frm.doc.sales_invoice },
		{ key: 'Paid',             label: 'Paid',      icon: '💰', done: frm.doc.status === 'Paid' },
		{ key: 'In Production',    label: 'Production',icon: '🍳', done: !!frm.doc.production_plan },
		{ key: 'Delivered',        label: 'Delivered', icon: '🚚', done: !!frm.doc.delivery_plan },
		{ key: 'Closed',           label: 'Closed',    icon: '🔒', done: frm.doc.status === 'Closed' },
	];

	const html = `
		<div style="padding:16px 8px;background:linear-gradient(to right,#fef9e7,#eaf2f8);border-radius:8px;">
			<div style="display:flex;justify-content:space-around;align-items:center;flex-wrap:wrap;gap:8px;">
				${stages.map((s, i) => `
					<div style="text-align:center;flex:1;min-width:80px;">
						<div style="
							width:48px;height:48px;line-height:48px;
							margin:0 auto 6px;border-radius:50%;
							background:${s.done ? '#27ae60' : '#bdc3c7'};
							color:white;font-size:22px;font-weight:bold;
							box-shadow:0 2px 4px rgba(0,0,0,0.1);
						">${s.icon}</div>
						<div style="font-size:11px;font-weight:${s.done ? 'bold' : 'normal'};color:${s.done ? '#1e8449' : '#7f8c8d'};">
							${s.label}
						</div>
					</div>
					${i < stages.length - 1 ? `<div style="flex:0;color:${stages[i+1].done ? '#27ae60' : '#bdc3c7'};font-size:18px;">→</div>` : ''}
				`).join('')}
			</div>
		</div>
	`;
	frm.fields_dict.workflow_dashboard?.$wrapper.html(html);
}

// ════════════════════════════════════════════════════════════════════════════
// PROFITABILITY CARD + DIALOG
// ════════════════════════════════════════════════════════════════════════════

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
					<div style="background:${margin_color};color:white;padding:16px;border-radius:8px;text-align:center;box-shadow:0 2px 4px rgba(0,0,0,0.1);">
						<div style="font-size:28px;font-weight:bold;">${d.gross_margin_percent}%</div>
						<div style="font-size:12px;opacity:0.9;">Gross Margin</div>
					</div>
					${_profit_card('Invoiced',      d.invoiced,   cur, '#9b59b6', '🧾')}
					${_profit_card('Paid',          d.paid,       cur, '#1abc9c', '✅')}
					${_profit_card('Outstanding',   d.outstanding,cur, '#c0392b', '⚠️')}
					${_profit_card('Wastage',       d.wastage,    cur, '#7f8c8d', '🗑️')}
				</div>
			`;
			frm.fields_dict.profitability_html?.$wrapper.html(html);
		}
	});
}

function _profit_card(label, value, cur, color, icon) {
	return `
		<div style="background:${color};color:white;padding:16px;border-radius:8px;text-align:center;box-shadow:0 2px 4px rgba(0,0,0,0.1);">
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
			const dialog = new frappe.ui.Dialog({
				title: __('Profitability — {0}', [frm.doc.name]),
				size: 'large',
				fields: [{ fieldtype: 'HTML', fieldname: 'pl', options: `
					<table class="table table-bordered" style="margin:0;">
						<tr style="background:#eaf2f8;font-weight:bold;"><td colspan="2">REVENUE</td></tr>
						<tr><td>Total Order Value</td><td style="text-align:right;">${format_currency(d.revenue, cur)}</td></tr>
						<tr><td>Invoiced</td><td style="text-align:right;">${format_currency(d.invoiced, cur)}</td></tr>
						<tr><td>Paid</td><td style="text-align:right;color:#27ae60;">${format_currency(d.paid, cur)}</td></tr>
						<tr><td>Outstanding</td><td style="text-align:right;color:#e74c3c;">${format_currency(d.outstanding, cur)}</td></tr>
						<tr style="background:#fef9e7;font-weight:bold;"><td colspan="2">COSTS</td></tr>
						<tr><td>Cost Sheet</td><td style="text-align:right;">${format_currency(d.cost, cur)}</td></tr>
						<tr><td>Wastage</td><td style="text-align:right;">${format_currency(d.wastage, cur)}</td></tr>
						<tr><td>Emergency</td><td style="text-align:right;">${format_currency(d.emergency, cur)}</td></tr>
						<tr style="font-weight:bold;background:#fadbd8;"><td>Total Cost</td><td style="text-align:right;">${format_currency(d.total_cost, cur)}</td></tr>
						<tr style="background:#eafaf1;font-weight:bold;font-size:16px;"><td>Gross Profit</td><td style="text-align:right;color:${d.gross_profit > 0 ? '#27ae60' : '#e74c3c'};">${format_currency(d.gross_profit, cur)}</td></tr>
						<tr style="background:#eafaf1;font-weight:bold;font-size:16px;"><td>Gross Margin %</td><td style="text-align:right;">${d.gross_margin_percent}%</td></tr>
					</table>` }]
			});
			dialog.show();
		}
	});
}

// ════════════════════════════════════════════════════════════════════════════
// MENU PACKAGE LOAD + CALCULATIONS
// ════════════════════════════════════════════════════════════════════════════

function _load_package_items(frm, pkg) {
	if (pkg.price_per_guest && !frm.doc.price_per_guest) {
		frm.set_value('price_per_guest', pkg.price_per_guest);
	}
	frm.clear_table('items');
	(pkg.items || []).forEach(pi => {
		const row = frm.add_child('items');
		row.item_code = pi.item_code;
		row.item_name = pi.item_name;
		row.category = pi.category;
		row.qty_per_guest = pi.qty_per_guest;
		row.uom = pi.uom;
		row.rate = pi.rate || 0;
		row.bom = pi.bom;
		row.is_manufactured = pi.is_manufactured;
		row.wastage_percent = pi.wastage_percent || 5;
		row.guest_count = frm.doc.total_guests;
		row.menu_package_item = pi.name;
		row.currency = frm.doc.currency;
	});
	frm.refresh_field('items');
	_recalc_totals(frm);
	frappe.show_alert({ message: __('Loaded {0} items', [(pkg.items || []).length]), indicator: 'green' });
}

function _recalc_item(frm, cdt, cdn) {
	const item = locals[cdt][cdn];
	const gc = item.guest_count || frm.doc.total_guests || 1;
	item.guest_count = gc;
	item.total_qty = (item.qty_per_guest || 0) * gc;
	item.amount = (item.total_qty || 0) * (item.rate || 0);
	frm.refresh_field('items');
	_recalc_totals(frm);
}

function _recalc_totals(frm) {
	let subtotal = 0;
	(frm.doc.items || []).forEach(it => { subtotal += (it.amount || 0); });
	(frm.doc.guest_types || []).forEach(g => { subtotal += (g.amount || 0); });
	frm.doc.subtotal = subtotal;
	frm.doc.discount_amount = subtotal * (frm.doc.discount_percent || 0) / 100;
	frm.doc.total_order_value = subtotal - frm.doc.discount_amount + (frm.doc.total_taxes || 0);
	frm.doc.deposit_amount = frm.doc.total_order_value * (frm.doc.deposit_percent || 0) / 100;
	frm.doc.balance_due = frm.doc.total_order_value - (frm.doc.total_paid || 0);
	['subtotal', 'discount_amount', 'total_order_value', 'deposit_amount', 'balance_due']
		.forEach(f => frm.refresh_field(f));
}

function _setup_status_indicator(frm) {
	const colors = {
		'Draft': 'gray', 'Quoted': 'orange', 'Confirmed': 'blue',
		'Deposit Received': 'cyan', 'In Production': 'purple',
		'Ready to Deliver': 'yellow', 'Delivered': 'green',
		'Invoiced': 'darkgreen', 'Paid': 'darkgreen',
		'Closed': 'darkgray', 'Cancelled': 'red'
	};
	const color = colors[frm.doc.status] || 'gray';
	frm.dashboard.set_headline_alert(
		`<strong>Status:</strong> ${frm.doc.status || 'Draft'}`, color
	);
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
