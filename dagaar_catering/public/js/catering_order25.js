
const _PROFITABILITY_ROLES = [
	'Catering Manager', 'Catering Management', 'Catering Finance Manager',
	'Catering Finance User', 'Catering Auditor', 'System Manager', 'Administrator'
];

function _can_see_profitability() {
	const user_roles = frappe.user_roles || [];
	return _PROFITABILITY_ROLES.some(r => user_roles.includes(r));
}

const _MANAGER_ROLES_JS = [
	'Catering Manager', 'Catering Management', 'System Manager', 'Administrator'
];

function _is_manager_js() {
	const user_roles = frappe.user_roles || [];
	return _MANAGER_ROLES_JS.some(r => user_roles.includes(r));
}

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
		_apply_production_lock(frm);
		_apply_void_freeze(frm);
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
		const row = locals[cdt][cdn];
		if (!row.menu_package) return;

		// #1: Check for duplicate selection across rows. If found, clear this
		// row's selection and warn — the same package can't appear twice.
		const dupe = (frm.doc.menu_packages || []).find(
			p => p.name !== row.name && p.menu_package === row.menu_package
		);
		if (dupe) {
			frappe.msgprint({
				title: __('Duplicate Package'),
				message: __('Menu Package <b>{0}</b> is already in row #{1}. ' +
				            'Combine the guest counts in a single row instead.',
				            [row.menu_package, dupe.idx]),
				indicator: 'red'
			});
			frappe.model.set_value(cdt, cdn, 'menu_package', '');
			return;
		}

		// Update this row's subtotal from package price × guest_count
		frappe.db.get_value('Catering Menu Package', row.menu_package,
			'price_per_guest', (r) => {
			if (r && r.price_per_guest && row.guest_count) {
				frappe.model.set_value(cdt, cdn, 'subtotal',
					flt(r.price_per_guest) * flt(row.guest_count));
			}
		});

		// #2: Add this package's items into the items table (don't wipe existing).
		// Match by item_code; skip items already in the table to avoid duplicates.
		frappe.call({
			method: 'dagaar_catering.catering_management.controllers.catering_order.get_items_for_package',
			args: { menu_package: row.menu_package },
			callback: (r) => {
				const pkg_items = r.message || [];
				if (!pkg_items.length) return;

				const existing_codes = new Set(
					(frm.doc.items || []).map(i => i.item_code)
				);
				const gc = flt(row.guest_count || 0);
				let added = 0;

				pkg_items.forEach(pi => {
					if (existing_codes.has(pi.item_code)) return;
					const new_row = frm.add_child('items');
					new_row.item_code = pi.item_code;
					new_row.item_name = pi.item_name || pi.item_code;
					new_row.qty_per_guest = flt(pi.qty_per_guest || 0);
					new_row.guest_count = gc;
					new_row.total_qty = flt(pi.qty_per_guest || 0) * gc;
					new_row.rate = flt(pi.rate || 0);
					new_row.amount = new_row.total_qty * new_row.rate;
					new_row.uom = pi.uom || 'Nos';
					added++;
				});

				if (added) {
					frm.refresh_field('items');
					frappe.show_alert({
						message: __('Added {0} item(s) from {1}', [added, row.menu_package]),
						indicator: 'blue'
					}, 4);
				}
			}
		});
	},

	guest_count: function(frm, cdt, cdn) {
		const row = locals[cdt][cdn];

		// Update row subtotal
		if (row.menu_package) {
			frappe.db.get_value('Catering Menu Package', row.menu_package,
				'price_per_guest', (r) => {
				if (r && r.price_per_guest) {
					frappe.model.set_value(cdt, cdn, 'subtotal',
						flt(r.price_per_guest) * flt(row.guest_count || 0));
				}
			});
		}

		// Recompute total_guests locally
		let total = 0;
		(frm.doc.menu_packages || []).forEach(p => total += (p.guest_count || 0));
		frm.doc.total_guests = total;
		frm.refresh_field('total_guests');

		// Sync items belonging to THIS package's master to the new guest_count
		if (!row.menu_package || !row.guest_count) return;
		frappe.call({
			method: 'dagaar_catering.catering_management.controllers.catering_order.get_items_for_package',
			args: { menu_package: row.menu_package },
			callback: (r) => {
				const pkg_item_codes = (r.message || []).map(x => x.item_code);
				if (!pkg_item_codes.length) return;
				const linked = (frm.doc.items || []).filter(
					it => pkg_item_codes.includes(it.item_code)
				);
				if (!linked.length) return;
				let updated = 0;
				linked.forEach(it => {
					frappe.model.set_value(it.doctype, it.name, 'guest_count',
						flt(row.guest_count));
					const new_total = flt(it.qty_per_guest) * flt(row.guest_count);
					frappe.model.set_value(it.doctype, it.name, 'total_qty', new_total);
					frappe.model.set_value(it.doctype, it.name, 'amount',
						new_total * flt(it.rate));
					updated++;
				});
				if (updated) {
					frm.refresh_field('items');
					frappe.show_alert({
						message: __('Synced {0} item(s) to {1} guests',
						            [updated, row.guest_count]),
						indicator: 'blue'
					}, 4);
				}
			}
		});
	},

	// When user REMOVES a menu_packages row:
	//   - Block removal if any of that package's items are already billed
	//     on a submitted Sales Invoice
	//   - Otherwise, remove all items belonging to that package from the items table
	before_menu_packages_remove: async function(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.menu_package) return;

		// Fetch this package's item codes from server
		const r = await frappe.call({
			method: 'dagaar_catering.catering_management.controllers.catering_order.get_items_for_package',
			args: { menu_package: row.menu_package },
		});
		const pkg_codes = (r.message || []).map(x => x.item_code);
		if (!pkg_codes.length) return;

		// Check if any of these items are on a submitted SI for this order
		if (frm.doc.name) {
			const chk = await frappe.call({
				method: 'dagaar_catering.catering_management.controllers.catering_order.check_items_invoiced',
				args: { catering_order: frm.doc.name, item_codes: pkg_codes },
			});
			const invoiced = chk.message || [];
			if (invoiced.length) {
				frappe.msgprint({
					title: __('Cannot Remove Package'),
					message: __('This package contains item(s) already billed on a submitted Sales Invoice: <b>{0}</b><br><br>' +
					            'Package removal is blocked to keep books in sync.',
					            [invoiced.join(', ')]),
					indicator: 'red'
				});
				frappe.validated = false;
				return false;
			}
		}

		// Safe — remove items from items table that belong to this package
		const to_remove = (frm.doc.items || []).filter(
			it => pkg_codes.includes(it.item_code)
		);
		let removed = 0;
		to_remove.forEach(it => {
			frm.get_field('items').grid.grid_rows.forEach(gr => {
				if (gr.doc.name === it.name) gr.remove();
			});
			removed++;
		});
		if (removed) {
			frm.refresh_field('items');
			frappe.show_alert({
				message: __('Removed {0} item(s) from {1}', [removed, row.menu_package]),
				indicator: 'orange'
			}, 4);
		}
	},
});

// Debounced auto-reload of items from packages.
// Fires only when:
//   - the doc has a name (saved at least once)
//   - the doc is not yet submitted (docstatus=0) AND no Production Plan exists
//   - menu_packages has at least one row
// Otherwise this is a silent no-op.
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

	qty_per_guest: function(frm) { _recalc_totals(frm); },
	total_qty: function(frm) { _recalc_totals(frm); },
	rate: function(frm) { _recalc_totals(frm); },
	amount: function(frm) { _recalc_totals(frm); },
	guest_count: function(frm) { _recalc_totals(frm); },
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



	// Void Catering Order - manager only, no payment yet, no production
	if (d.docstatus === 1 && _is_manager_js() &&
	    !d.production_plan &&
	    !['Closed', 'Cancelled', 'Void'].includes(d.status)) {
		frappe.db.count('Payment Entry', {
			filters: { catering_order: d.name, docstatus: 1 }
		}).then(pay_count => {
			if (pay_count === 0) {
				frm.add_custom_button(__('⛔ Void Order'), () => {
					frappe.prompt({
						fieldname: 'reason', label: __('Reason (optional)'),
						fieldtype: 'Small Text',
					}, (values) => {
						frappe.confirm(
							__('Void this order? It will be disabled with no further actions.'),
							() => {
								frappe.call({
									method: 'dagaar_catering.catering_management.controllers.catering_order.void_catering_order',
									args: { catering_order: frm.doc.name, reason: values.reason || '' },
									callback: () => frm.reload_doc(),
								});
							}
						);
					}, __('Void Order'), __('Continue'));
				}, __('Status')).addClass('btn-danger');
			}
		});
	}

	// 💵 Quick Expense — always available on submitted, active orders
	if (d.docstatus === 1 && d.status !== 'Closed' && d.status !== 'Void') {
		frm.add_custom_button(__('💵 Quick Expense'),
			() => _show_quick_expense_dialog(frm), __('Create'));
	}

	// 🛎 Additional Service — only after Sales Order AND Sales Invoice are created
	// (additional services are billed separately, but the primary invoicing flow
	// must be in place first to avoid confusion)
	if (d.docstatus === 1 && d.status !== 'Closed' && d.status !== 'Void'
	    && d.sales_order && d.sales_invoice) {
		frm.add_custom_button(__('🛎 Additional Service'),
			() => _show_additional_service_dialog(frm), __('Create'));
	}

	if (d.docstatus !== 1) return;
	if (['Closed', 'Void', 'Cancelled'].includes(d.status)) return;

	const has_items = d.items && d.items.length > 0;

	if (!d.sales_order && has_items) {
		frm.add_custom_button(__('Sales Order'),
			() => _show_sales_order_dialog(frm), __('Create'));
	}
	// Sales Invoice button — simple: show when sales_order exists and no
	// package SI exists yet. Server's _get_package_sales_invoice ensures
	// additional service SIs don't count.
	if (d.sales_order && !d.sales_invoice) {
		frm.add_custom_button(__('Sales Invoice'),
			() => _show_sales_invoice_dialog(frm), __('Create'));
	}
	// Payment Entry button — appears whenever ANY linked Sales Invoice has
	// outstanding balance (unpaid, partly paid, or overdue). Covers package SI,
	// supplementary SIs, AND additional service SIs. No gate on co.sales_invoice
	// because additional service SIs may exist before any package SI.
	frappe.db.count('Sales Invoice', {
		filters: {
			catering_order: d.name,
			docstatus: 1,
			outstanding_amount: ['>', 0.01],
			is_return: 0,
		}
	}).then(cnt => {
		if (cnt > 0) {
			frm.add_custom_button(__('Payment Entry'),
				() => _show_payment_entry_dialog(frm), __('Create'));
		}
	});
	if (d.sales_invoice && !d.production_plan) {
		frm.add_custom_button(__('Production Plan'),
			() => _action(frm, 'create_production_plan'), __('Create'));
	}
	// Delivery Plan button: requires at least one submitted Manufacture Stock Entry
	if (d.sales_invoice && !d.delivery_plan) {
		frappe.db.count('Stock Entry', {
			filters: {
				catering_order: d.name,
				purpose: 'Manufacture',
				docstatus: 1,
			}
		}).then(cnt => {
			if (cnt > 0) {
				frm.add_custom_button(__('Delivery Plan'),
					() => _action(frm, 'create_delivery_plan'), __('Create'));
			} else if (d.production_plan) {
				// PP exists but no finished manufacture yet — show informational tip
				frm.dashboard.add_indicator(
					__('⏳ Production in progress — complete Manufacture Stock Entry to enable Delivery Plan'),
					'orange'
				);
			}
		});
	}
	// Closing Sheet — only after a submitted Delivery Note exists for this order.
	// Confirms physical delivery has happened before we let user close the books.
	if (d.sales_invoice && d.delivery_plan && !d.closing_sheet) {
		frappe.db.count('Delivery Note', {
			filters: { catering_order: d.name, docstatus: 1 }
		}).then(dn_count => {
			if (dn_count > 0) {
				frm.add_custom_button(__('Closing Sheet'),
					() => _action(frm, 'create_closing_sheet'), __('Create'));
			}
		});
	}

	// Regenerate Bill — LIVE check: compares order_total to net_billed via
	// the package SI. Shows whenever a package SI exists and amounts differ.
	// No reliance on requires_rebill flag (which can be stale).
	if (d.sales_invoice) {
		frappe.call({
			method: 'dagaar_catering.catering_management.controllers.catering_order.get_billing_status',
			args: { catering_order: d.name },
			callback: (br) => {
				const bs = br.message || {};
				// Show only if a PACKAGE SI exists (not just an additional service SI)
				// AND there's a non-trivial difference between order total and net billed
				if (bs.has_sales_invoice && Math.abs(bs.unbilled_amount || 0) > 0.01) {
					const diff = bs.unbilled_amount;
					const dir = diff > 0 ? 'under-billed' : 'over-billed';
					frm.add_custom_button(__('⚠️ Regenerate Bill'), () => {
						frappe.confirm(
							__('The order was amended since the last invoice.<br><br>' +
							   'Current order total: <b>{0}</b><br>' +
							   'Net billed so far: <b>{1}</b><br>' +
							   'Unbilled difference: <b style="color:{2};">{3}</b> ({4})<br><br>' +
							   'This will sync the Sales Invoice. ' +
							   'Sales Order, Work Orders, and Payments stay intact. Continue?',
							   [
								   format_currency(bs.order_total, bs.currency),
								   format_currency(bs.net_billed, bs.currency),
								   diff > 0 ? '#c0392b' : '#27ae60',
								   format_currency(Math.abs(diff), bs.currency),
								   dir
							   ]),
							() => {
								frappe.call({
									method: 'dagaar_catering.catering_management.controllers.catering_order.update_sales_invoice',
									args: { catering_order: frm.doc.name },
									freeze: true, freeze_message: __('Regenerating bill...'),
									callback: () => {
										frm.reload_doc();
										frappe.show_alert({
											message: __('Bill regenerated'), indicator: 'green'
										}, 5);
									},
								});
							}
						);
					}).addClass('btn-danger').css({'font-weight': 'bold'});
				}
			}
		});
	}

	frm.add_custom_button(__('Wastage'),
		() => _record_doc(frm, 'Catering Wastage Entry'), __('Record'));
	frm.add_custom_button(__('Return'),
		() => _record_doc(frm, 'Catering Return Entry'), __('Record'));
	if (_can_see_profitability()) {
		frm.add_custom_button(__('Profitability'),
			() => _show_profitability_dialog(frm), __('View'));
		frm.add_custom_button(__('Profitability Report'), () => {
			frappe.set_route('query-report', 'Catering Order Profitability',
				{ catering_order: frm.doc.name });
		}, __('View'));
	}
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
								_open_in_new_tab(frm, 'Journal Entry', r.message.journal_entry);
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
		method: 'dagaar_catering.catering_management.controllers.catering_order.get_unpaid_invoices',
		args: { catering_order: frm.doc.name },
		callback: (r) => {
			const invoices = r.message || [];
			if (!invoices.length) {
				frappe.msgprint(__('No unpaid invoices found for this order.'));
				return;
			}
			const cur = invoices[0].currency || frm.doc.currency || 'USD';
			const init_total = invoices.reduce((s, i) => s + i.outstanding_amount, 0);

			const dialog = new frappe.ui.Dialog({
				title: __('Record Payment'),
				size: 'large',
				fields: [
					{
						fieldname: 'invoices_html', fieldtype: 'HTML',
						options: `
							<div style="margin-bottom:8px;color:#555;">
								Select which invoices to pay. Edit each amount or uncheck to skip.
							</div>
							<table class="table table-bordered" style="font-size:12px;">
								<thead style="background:#f5f5f5;">
									<tr>
										<th style="width:30px;">Pay</th>
										<th>Invoice</th>
										<th>Date</th>
										<th style="text-align:right;">Grand Total</th>
										<th style="text-align:right;">Outstanding</th>
										<th style="text-align:right;width:130px;">Pay Amount</th>
									</tr>
								</thead>
								<tbody id="qe-inv-rows">
									${invoices.map((inv, i) => `
										<tr>
											<td style="text-align:center;">
												<input type="checkbox" class="qe-inv-check" data-idx="${i}" checked>
											</td>
											<td>${frappe.utils.escape_html(inv.name)}</td>
											<td>${frappe.datetime.str_to_user(inv.posting_date)}</td>
											<td style="text-align:right;">${format_currency(inv.grand_total, cur)}</td>
											<td style="text-align:right;color:#c0392b;">${format_currency(inv.outstanding_amount, cur)}</td>
											<td>
												<input type="number" class="qe-inv-amount form-control" data-idx="${i}"
												       value="${inv.outstanding_amount}"
												       max="${inv.outstanding_amount}" min="0" step="0.01"
												       style="text-align:right;">
											</td>
										</tr>
									`).join('')}
								</tbody>
								<tfoot>
									<tr style="background:#fffbe6;font-weight:bold;">
										<td colspan="5" style="text-align:right;">Total to pay:</td>
										<td style="text-align:right;" id="qe-total">${format_currency(init_total, cur)}</td>
									</tr>
								</tfoot>
							</table>
						`
					},
					{ fieldtype: 'Section Break' },
					{ fieldname: 'mode_of_payment', fieldtype: 'Link', options: 'Mode of Payment',
					  label: __('Mode of Payment'), reqd: 1 },
					{ fieldname: 'paid_to', fieldtype: 'Link', options: 'Account',
					  label: __('Paid To (Bank/Cash)'), reqd: 1,
					  get_query: () => ({
						  filters: {
							  account_type: ['in', ['Bank', 'Cash']],
							  company: frm.doc.company,
							  is_group: 0,
						  }
					  }) },
					{ fieldtype: 'Column Break' },
					{ fieldname: 'reference_no', fieldtype: 'Data', label: __('Reference No.') },
					{ fieldname: 'reference_date', fieldtype: 'Date',
					  label: __('Reference Date'), default: frappe.datetime.get_today() },
					{ fieldname: 'auto_submit', fieldtype: 'Check',
					  label: __('Auto-submit Payment Entry'), default: 1 },
				],
				primary_action_label: __('Record Payment'),
				primary_action: (values) => {
					const allocations = [];
					dialog.$wrapper.find('#qe-inv-rows tr').each(function(i) {
						const checked = $(this).find('.qe-inv-check').is(':checked');
						const amt = parseFloat($(this).find('.qe-inv-amount').val()) || 0;
						if (checked && amt > 0) {
							allocations.push({ invoice: invoices[i].name, amount: amt });
						}
					});
					if (!allocations.length) {
						frappe.msgprint(__('Select at least one invoice with a non-zero amount.'));
						return;
					}
					dialog.hide();
					frappe.call({
						method: 'dagaar_catering.catering_management.controllers.catering_order.create_payment_entry',
						args: {
							catering_order: frm.doc.name,
							allocations: JSON.stringify(allocations),
							mode_of_payment: values.mode_of_payment,
							paid_to: values.paid_to,
							reference_no: values.reference_no,
							reference_date: values.reference_date,
							auto_submit: values.auto_submit ? 1 : 0,
						},
						freeze: true,
						freeze_message: __('Recording payment...'),
						callback: (r) => {
							if (r.message) _open_in_new_tab(frm, 'Payment Entry', r.message);
						},
					});
				}
			});

			// Live recompute of total as user ticks/edits
			setTimeout(() => {
				dialog.$wrapper.find('.qe-inv-check, .qe-inv-amount').on('change keyup', () => {
					let total = 0;
					dialog.$wrapper.find('#qe-inv-rows tr').each(function() {
						const checked = $(this).find('.qe-inv-check').is(':checked');
						const amt = parseFloat($(this).find('.qe-inv-amount').val()) || 0;
						if (checked) total += amt;
					});
					dialog.$wrapper.find('#qe-total').text(format_currency(total, cur));
				});
			}, 200);
			dialog.show();
		}
	});
}

function _open_in_new_tab(frm, doctype, docname) {
	// Some doctypes keep their old "open in new tab" behaviour (per user request):
	//   - Catering Production Plan
	//   - Catering Delivery Plan
	//   - Catering Closing Sheet
	// Everything else (Sales Order, Sales Invoice, Payment Entry, Journal Entry):
	//   silent popup with Print / Open / Close buttons.
	const OPEN_IN_NEW_TAB = [
		'Catering Production Plan',
		'Catering Delivery Plan',
		'Catering Closing Sheet',
	];

	if (OPEN_IN_NEW_TAB.includes(doctype)) {
		// Old behaviour: alert + open in new tab + reload form
		frappe.show_alert({
			message: __('Created {0} {1}', [doctype, docname]),
			indicator: 'green'
		}, 5);
		const url = `/app/${frappe.router.slug(doctype)}/${encodeURIComponent(docname)}`;
		window.open(url, '_blank');
		setTimeout(() => frm.reload_doc(), 500);
		return;
	}

	// Silent popup for everything else
	const dialog = new frappe.ui.Dialog({
		title: __('{0} Created', [doctype]),
		fields: [
			{
				fieldname: 'msg', fieldtype: 'HTML',
				options: `
					<div style="text-align:center;padding:20px 0;">
						<div style="font-size:48px;line-height:1;">✅</div>
						<h3 style="margin:10px 0 4px;">${frappe.utils.escape_html(doctype)}</h3>
						<p style="font-size:16px;color:#2e7d32;font-weight:600;margin:6px 0;">
							${frappe.utils.escape_html(docname)}
						</p>
						<p style="color:#666;font-size:13px;">has been created and submitted.</p>
					</div>
				`
			}
		],
		primary_action_label: __('Print {0}', [doctype]),
		primary_action: () => {
			window.open(`/api/method/frappe.utils.print_format.download_pdf?doctype=${encodeURIComponent(doctype)}&name=${encodeURIComponent(docname)}&format=Standard&no_letterhead=0`, '_blank');
		},
		secondary_action_label: __('Open Document'),
		secondary_action: () => {
			window.open(`/app/${frappe.router.slug(doctype)}/${encodeURIComponent(docname)}`, '_blank');
			dialog.hide();
		},
	});
	dialog.show();
	setTimeout(() => frm.reload_doc(), 200);
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
		{ label: 'Draft',       done: true },
		{ label: 'Submitted',   done: frm.doc.docstatus >= 1 },
		{ label: 'SO',          done: !!frm.doc.sales_order },
		{ label: 'Invoice',     done: !!frm.doc.sales_invoice },
		{ label: 'Paid',        done: frm.doc.status === 'Paid' },
		{ label: 'Production',  done: !!frm.doc.production_plan },
		{ label: 'Delivered',   done: frm.doc.status === 'Delivered' || !!frm.doc.delivery_note },
		{ label: 'Closed',      done: frm.doc.status === 'Closed' },
	];

	const completed = stages.filter(s => s.done).length;
	const total = stages.length;
	const pct = Math.round((completed / total) * 100);

	// Slim inline bar: 6px tall bar with a progress fill, segments separated by ticks
	const html = `
		<div style="padding:4px 2px;">
			<div style="display:flex;justify-content:space-between;font-size:10px;color:#666;margin-bottom:3px;">
				${stages.map(s => `<span style="color:${s.done ? '#27ae60' : '#bdc3c7'};font-weight:${s.done ? '600' : '400'};">${s.label}</span>`).join('')}
			</div>
			<div style="position:relative;height:6px;background:#ecf0f1;border-radius:3px;overflow:hidden;">
				<div style="position:absolute;left:0;top:0;height:100%;width:${pct}%;background:linear-gradient(to right,#27ae60,#16a085);border-radius:3px;transition:width 0.4s;"></div>
				${stages.map((s, i) => {
					const left = ((i + 1) / total) * 100;
					return i < total - 1
						? `<div style="position:absolute;left:${left}%;top:0;height:100%;width:1px;background:rgba(255,255,255,0.6);"></div>`
						: '';
				}).join('')}
			</div>
			<div style="text-align:right;font-size:10px;color:#999;margin-top:2px;">
				${completed} / ${total} stages
			</div>
		</div>
	`;
	frm.fields_dict.workflow_dashboard?.$wrapper.html(html);
}

function _render_profitability_card(frm) {
	if (frm.is_new() || !frm.doc.name) return;

	// Hide the entire profitability area for users without finance roles
	if (!_can_see_profitability()) {
		if (frm.fields_dict.profitability_html?.$wrapper) {
			frm.fields_dict.profitability_html.$wrapper.html('');
		}
		frm.toggle_display('profitability_section', false);
		frm.toggle_display('profitability_html', false);
		return;
	}

	frappe.call({
		method: 'dagaar_catering.catering_management.controllers.catering_order.get_profitability',
		args: { catering_order: frm.doc.name },
		callback: (r) => {
			if (!r.message) return;
			const d = r.message;
			const cur = d.currency || frm.doc.currency || 'USD';
			const margin_color = d.gross_margin_percent >= 20 ? '#27ae60'
				: d.gross_margin_percent >= 10 ? '#f39c12' : '#e74c3c';
			// Compact cards — 100px wide, smaller font
			const html = `
				<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:6px;padding:4px;">
					${_profit_card_sm('Revenue',     d.revenue,    cur, '#3498db', '💰')}
					${_profit_card_sm('Cost',        d.total_cost, cur, '#e67e22', '💸')}
					${_profit_card_sm('Profit',      d.gross_profit, cur, '#27ae60', '📈')}
					<div style="background:${margin_color};color:white;padding:8px 6px;border-radius:4px;text-align:center;">
						<div style="font-size:18px;font-weight:bold;">${d.gross_margin_percent}%</div>
						<div style="font-size:10px;opacity:0.9;">Margin</div>
					</div>
					${_profit_card_sm('Invoiced',    d.invoiced,   cur, '#9b59b6', '🧾')}
					${_profit_card_sm('Paid',        d.paid,       cur, '#1abc9c', '✅')}
					${_profit_card_sm('Outstanding', d.outstanding,cur, '#c0392b', '⚠️')}
					${_profit_card_sm('Wastage',     d.wastage,    cur, '#7f8c8d', '🗑️')}
				</div>
			`;
			frm.fields_dict.profitability_html?.$wrapper.html(html);
		}
	});
}

function _profit_card_sm(label, value, cur, color, icon) {
	return `
		<div style="background:${color};color:white;padding:8px 6px;border-radius:4px;text-align:center;">
			<div style="font-size:12px;">${icon}</div>
			<div style="font-size:13px;font-weight:bold;margin-top:2px;line-height:1.2;">${format_currency(value, cur)}</div>
			<div style="font-size:10px;opacity:0.9;margin-top:1px;">${label}</div>
		</div>`;
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

function _apply_production_lock(frm) {
	// If Production Plan exists and order is submitted, lock menu_packages, items, total_guests.
	// Managers can bypass via the "Unlock for edit" button (manual override).
	if (frm.is_new()) return;
	if (frm.doc.docstatus !== 1) return;
	if (!frm.doc.production_plan) return;

	const locked_fields = ['menu_packages', 'items', 'total_guests'];
	locked_fields.forEach(f => {
		if (frm.fields_dict[f]) {
			frm.set_df_property(f, 'read_only', 1);
		}
	});

	// Make child grids non-editable
	if (frm.fields_dict.menu_packages?.grid) {
		frm.fields_dict.menu_packages.grid.cannot_add_rows = true;
		frm.fields_dict.menu_packages.grid.df.cannot_delete_rows = true;
		frm.fields_dict.menu_packages.grid.refresh();
	}
	if (frm.fields_dict.items?.grid) {
		frm.fields_dict.items.grid.cannot_add_rows = true;
		frm.fields_dict.items.grid.df.cannot_delete_rows = true;
		frm.fields_dict.items.grid.refresh();
	}

	frm.dashboard.add_indicator(
		__('🔒 Locked: Production Plan created. To change items or guests, create a new Catering Order.'),
		'red'
	);
}


function _show_additional_service_dialog(frm) {
	// Creates a fresh Sales Invoice (NOT supplementary) for services only,
	// using items filtered by item_group='Service'. Each service charge is a
	// separate SI tagged with the catering order so it shows in revenue.
	const dialog = new frappe.ui.Dialog({
		title: __('Additional Service Charge'),
		size: 'large',
		fields: [
			{
				fieldname: 'header', fieldtype: 'HTML',
				options: `<div style="background:#f4f7fa;padding:10px 14px;border-left:3px solid #16a085;border-radius:4px;margin-bottom:6px;">
					Creates a <b>new Sales Invoice</b> for service charges (e.g. setup fee, late-night surcharge). Only Items in the <b>Service</b> item group are allowed.
				</div>`
			},
			{
				fieldname: 'service_item', fieldtype: 'Link', options: 'Item',
				label: __('Service Item'), reqd: 1,
				get_query: () => ({ filters: { item_group: 'Service' } })
			},
			{ fieldname: 'col1', fieldtype: 'Column Break' },
			{ fieldname: 'qty', fieldtype: 'Float',
			  label: __('Quantity'), reqd: 1, default: 1 },
			{ fieldname: 'sec1', fieldtype: 'Section Break' },
			{ fieldname: 'rate', fieldtype: 'Currency',
			  label: __('Rate'), reqd: 1 },
			{ fieldname: 'col2', fieldtype: 'Column Break' },
			{ fieldname: 'description', fieldtype: 'Small Text',
			  label: __('Notes (optional)') },
		],
		primary_action_label: __('Create Invoice'),
		primary_action: (values) => {
			dialog.hide();
			frappe.call({
				method: 'dagaar_catering.catering_management.controllers.catering_order.create_additional_service_invoice',
				args: {
					catering_order: frm.doc.name,
					service_item: values.service_item,
					qty: values.qty,
					rate: values.rate,
					description: values.description || '',
				},
				freeze: true, freeze_message: __('Creating service invoice...'),
				callback: (r) => {
					if (r.message) {
						_open_in_new_tab(frm, 'Sales Invoice', r.message);
					}
				}
			});
		}
	});
	dialog.show();
}

function _apply_void_freeze(frm) {
	if (frm.is_new()) return;
	const terminal = ['Void', 'Closed', 'Cancelled'];
	if (!terminal.includes(frm.doc.status)) return;
	frm.disable_save();
	frm.set_read_only();
	const icons = { 'Void': '⛔', 'Closed': '🔒', 'Cancelled': '❌' };
	const colors = { 'Void': 'orange', 'Closed': 'gray', 'Cancelled': 'red' };
	frm.dashboard.add_indicator(
		__('{0} Order is {1} - no further changes allowed', [
			icons[frm.doc.status], frm.doc.status
		]), colors[frm.doc.status] || 'gray'
	);
}

// ─── Live totals recalculation on table changes ─────────────────────────
// Fires _recalc_totals whenever a row is added/removed/changed in the
// menu_packages or items child tables.
frappe.ui.form.on('Catering Order', {
	items_add: function(frm) { _recalc_totals(frm); },
	items_remove: function(frm) { _recalc_totals(frm); },
	menu_packages_add: function(frm) { _recalc_totals(frm); },
	menu_packages_remove: function(frm) { _recalc_totals(frm); },
	discount_percent: function(frm) { _recalc_totals(frm); },
	discount_amount: function(frm) { _recalc_totals(frm); },
	total_taxes: function(frm) { _recalc_totals(frm); },
	deposit_percent: function(frm) { _recalc_totals(frm); },
	total_paid: function(frm) { _recalc_totals(frm); },
});
