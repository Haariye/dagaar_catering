// Copyright (c) 2024, DagaarSoft and contributors
// License: MIT
//
// Catering Order — Enterprise Client Script v2.0
// Provides workflow-driven UI: action buttons, status indicators,
// profitability dashboard, package auto-load, real-time totals.

frappe.ui.form.on('Catering Order', {

	refresh: function(frm) {
		_render_workflow_progress(frm);
		_render_profitability_card(frm);
		_setup_action_buttons(frm);
		_setup_status_indicator(frm);
		_setup_quick_links(frm);
	},

	customer: function(frm) {
		// Auto-fetch customer details
		if (frm.doc.customer) {
			frappe.db.get_value('Customer', frm.doc.customer,
				['customer_name', 'customer_group', 'territory', 'default_currency'], (r) => {
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
				__('Load items from selected Menu Package? This will replace any existing items.'),
				() => {
					frappe.call({
						method: 'frappe.client.get',
						args: { doctype: 'Catering Menu Package', name: frm.doc.menu_package },
						callback: (r) => {
							if (r.message) {
								_load_package_items(frm, r.message);
							}
						}
					});
				}
			);
		}
	},

	total_guests: function(frm) {
		// Cascade guest count to all items that don't have an override
		(frm.doc.items || []).forEach((item, idx) => {
			if (!item.guest_count || item.guest_count === item.__prev_guest_count) {
				frappe.model.set_value(item.doctype, item.name, 'guest_count', frm.doc.total_guests);
			}
		});
	},

	deposit_percent: function(frm) {
		const deposit = (frm.doc.total_order_value || 0) * (frm.doc.deposit_percent || 0) / 100;
		frm.set_value('deposit_amount', deposit);
	},

	discount_percent: function(frm) {
		_recalc_totals(frm);
	},
});

frappe.ui.form.on('Catering Order Item', {
	qty_per_guest: function(frm, cdt, cdn) { _recalc_item(frm, cdt, cdn); },
	guest_count: function(frm, cdt, cdn) { _recalc_item(frm, cdt, cdn); },
	rate: function(frm, cdt, cdn) { _recalc_item(frm, cdt, cdn); },
});

// ════════════════════════════════════════════════════════════════════════════
// WORKFLOW PROGRESS DASHBOARD
// ════════════════════════════════════════════════════════════════════════════

function _render_workflow_progress(frm) {
	if (frm.is_new()) return;

	const stages = [
		{ key: 'Draft',            label: 'Draft',     icon: '📝', done: true },
		{ key: 'Quoted',           label: 'Quoted',    icon: '📄', done: !!frm.doc.quotation },
		{ key: 'Confirmed',        label: 'SO',        icon: '✅', done: !!frm.doc.sales_order },
		{ key: 'Deposit Received', label: 'Deposit',   icon: '💵', done: (frm.doc.deposit_received || 0) >= (frm.doc.deposit_amount || 0) && (frm.doc.deposit_amount || 0) > 0 },
		{ key: 'In Production',    label: 'Production',icon: '🍳', done: !!frm.doc.production_plan },
		{ key: 'Delivered',        label: 'Delivered', icon: '🚚', done: !!frm.doc.delivery_note || (frm.doc.delivery_plan && _check_dp_delivered(frm)) },
		{ key: 'Invoiced',         label: 'Invoice',   icon: '🧾', done: !!frm.doc.sales_invoice },
		{ key: 'Paid',             label: 'Paid',      icon: '💰', done: frm.doc.status === 'Paid' || (frm.doc.balance_due === 0 && frm.doc.total_order_value > 0) },
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

function _check_dp_delivered(frm) {
	// Stale check from refresh — best-effort
	return false;
}

// ════════════════════════════════════════════════════════════════════════════
// LIVE PROFITABILITY CARD
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

// ════════════════════════════════════════════════════════════════════════════
// ACTION BUTTONS — workflow-driven
// ════════════════════════════════════════════════════════════════════════════

function _setup_action_buttons(frm) {
	if (frm.is_new()) return;
	if (frm.doc.docstatus === 2) return;
	if (frm.doc.status === 'Closed') return;

	const d = frm.doc;
	const can_create = (cond) => frm.doc.docstatus !== 2 && cond;

	// Group: Create
	if (!d.quotation && d.items && d.items.length > 0) {
		frm.add_custom_button(__('Quotation'), () => _action(frm, 'create_quotation'), __('Create'));
	}
	if (d.quotation && !d.sales_order) {
		frm.add_custom_button(__('Sales Order'), () => _action(frm, 'create_sales_order'), __('Create'));
	}
	if (d.sales_order && (d.deposit_received || 0) < (d.deposit_amount || 0)) {
		frm.add_custom_button(__('Deposit Payment'), () => _action(frm, 'create_deposit_payment'), __('Create'));
	}
	if (!d.cost_sheet) {
		frm.add_custom_button(__('Cost Sheet'), () => _action(frm, 'create_cost_sheet'), __('Create'));
	}
	if (!d.production_plan) {
		frm.add_custom_button(__('Production Plan'), () => _action(frm, 'create_production_plan'), __('Create'));
	}
	if (!d.material_request) {
		frm.add_custom_button(__('Material Request'), () => _action(frm, 'create_material_request'), __('Create'));
	}
	if (!d.delivery_plan) {
		frm.add_custom_button(__('Delivery Plan'), () => _action(frm, 'create_delivery_plan'), __('Create'));
	}
	if (d.production_plan && !d.sales_invoice) {
		frm.add_custom_button(__('Sales Invoice'), () => _action(frm, 'create_sales_invoice'), __('Create'));
	}
	if (d.sales_invoice && !d.closing_sheet) {
		frm.add_custom_button(__('Closing Sheet'), () => _action(frm, 'create_closing_sheet'), __('Create'));
	}

	// Group: Record
	frm.add_custom_button(__('Wastage'), () => _record_doc(frm, 'Catering Wastage Entry'), __('Record'));
	frm.add_custom_button(__('Return'), () => _record_doc(frm, 'Catering Return Entry'), __('Record'));
	frm.add_custom_button(__('Emergency Expense'), () => _record_doc(frm, 'Catering Emergency Expense'), __('Record'));

	// Group: View
	frm.add_custom_button(__('Profitability'), () => _show_profitability_dialog(frm), __('View'));

	// Top-level: Close Order
	if (d.closing_sheet && d.status !== 'Closed') {
		frm.add_custom_button(__('🔒 Close Order'), () => {
			frappe.confirm(
				__('Close this Catering Order? This action requires the Closing Sheet to be approved.'),
				() => _action(frm, 'close_catering_order')
			);
		}).addClass('btn-danger');
	}
}

function _action(frm, method) {
	frappe.call({
		method: `dagaar_catering.catering_management.controllers.catering_order.${method}`,
		args: { catering_order: frm.doc.name },
		freeze: true,
		freeze_message: __('Processing...'),
		callback: (r) => {
			if (r.message) {
				frm.reload_doc();
			}
		}
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
// PROFITABILITY DIALOG
// ════════════════════════════════════════════════════════════════════════════

function _show_profitability_dialog(frm) {
	frappe.call({
		method: 'dagaar_catering.catering_management.controllers.catering_order.get_profitability',
		args: { catering_order: frm.doc.name },
		callback: (r) => {
			if (!r.message) return;
			const d = r.message;
			const cur = d.currency;

			const dialog = new frappe.ui.Dialog({
				title: __('Profitability Snapshot — {0}', [frm.doc.name]),
				size: 'large',
				fields: [{
					fieldtype: 'HTML',
					fieldname: 'pl_html',
					options: `
						<table class="table table-bordered" style="margin:0;">
							<tbody>
								<tr style="background:#eaf2f8;font-weight:bold;"><td colspan="2">REVENUE</td></tr>
								<tr><td>Total Order Value</td><td style="text-align:right;">${format_currency(d.revenue, cur)}</td></tr>
								<tr><td>Invoiced</td><td style="text-align:right;">${format_currency(d.invoiced, cur)}</td></tr>
								<tr><td>Paid</td><td style="text-align:right;color:#27ae60;">${format_currency(d.paid, cur)}</td></tr>
								<tr><td>Outstanding</td><td style="text-align:right;color:#e74c3c;">${format_currency(d.outstanding, cur)}</td></tr>

								<tr style="background:#fef9e7;font-weight:bold;"><td colspan="2">COSTS</td></tr>
								<tr><td>Cost Sheet Total</td><td style="text-align:right;">${format_currency(d.cost, cur)}</td></tr>
								<tr><td>Wastage</td><td style="text-align:right;">${format_currency(d.wastage, cur)}</td></tr>
								<tr><td>Emergency Expenses</td><td style="text-align:right;">${format_currency(d.emergency, cur)}</td></tr>
								<tr style="font-weight:bold;background:#fadbd8;"><td>Total Cost</td><td style="text-align:right;">${format_currency(d.total_cost, cur)}</td></tr>

								<tr style="background:#eafaf1;font-weight:bold;"><td colspan="2">PROFIT & LOSS</td></tr>
								<tr style="font-weight:bold;font-size:16px;"><td>Gross Profit</td><td style="text-align:right;color:${d.gross_profit > 0 ? '#27ae60' : '#e74c3c'};">${format_currency(d.gross_profit, cur)}</td></tr>
								<tr style="font-weight:bold;font-size:16px;"><td>Gross Margin %</td><td style="text-align:right;color:${d.gross_margin_percent >= 20 ? '#27ae60' : d.gross_margin_percent >= 10 ? '#f39c12' : '#e74c3c'};">${d.gross_margin_percent}%</td></tr>
							</tbody>
						</table>
					`
				}]
			});
			dialog.show();
		}
	});
}

// ════════════════════════════════════════════════════════════════════════════
// MENU PACKAGE LOAD
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
	frappe.show_alert({ message: __('Loaded {0} items from package', [(pkg.items || []).length]), indicator: 'green' });
}

// ════════════════════════════════════════════════════════════════════════════
// CALCULATIONS
// ════════════════════════════════════════════════════════════════════════════

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

	['subtotal', 'discount_amount', 'total_order_value', 'deposit_amount', 'balance_due'].forEach(f => {
		frm.refresh_field(f);
	});
}

// ════════════════════════════════════════════════════════════════════════════
// STATUS INDICATOR
// ════════════════════════════════════════════════════════════════════════════

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
		`<div style="padding:4px 8px;"><strong>Status:</strong> <span style="color:${color === 'darkgray' ? '#555' : ''};">${frm.doc.status || 'Draft'}</span></div>`,
		color
	);
}

// ════════════════════════════════════════════════════════════════════════════
// QUICK LINKS
// ════════════════════════════════════════════════════════════════════════════

function _setup_quick_links(frm) {
	if (frm.is_new()) return;

	const links = [
		{ field: 'quotation',       dt: 'Quotation',                label: __('Open Quotation') },
		{ field: 'sales_order',     dt: 'Sales Order',              label: __('Open Sales Order') },
		{ field: 'sales_invoice',   dt: 'Sales Invoice',            label: __('Open Sales Invoice') },
		{ field: 'cost_sheet',      dt: 'Catering Cost Sheet',      label: __('Open Cost Sheet') },
		{ field: 'production_plan', dt: 'Catering Production Plan', label: __('Open Production Plan') },
		{ field: 'closing_sheet',   dt: 'Catering Closing Sheet',   label: __('Open Closing Sheet') },
	];

	links.forEach(l => {
		if (frm.doc[l.field]) {
			frm.add_custom_button(l.label, () => {
				frappe.set_route('Form', l.dt, frm.doc[l.field]);
			}, __('Open'));
		}
	});
}
