// Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt
frappe.ui.form.on('Quoting Sheet', {
	onload: (frm) => {
		frm.set_query("bom", () => {
			if (frm.doc.item_code) {
				return {
					filters: {
						"item": frm.doc.item_code
					}
				};
			}
		});
	},
	bom: function (frm) {
		if (frm.doc.bom) {
			frappe.call({
				method: "get_raw_materials",
				doc: frm.doc,
				callback: (res) => {
					console.log("BITCH============", res)
					// frm.doc.raw_material_items = res.message
					frm.refresh_field("raw_material_items")
				},
			})
		}
	},


	update_rate: function (frm) {
		frappe.call({
			method: "erpnext.selling.doctype.quoting_sheet.quoting_sheet.update_latest_rate",
			args: {
				"docname": frm.doc.name
			},
			callback: () => {
				frm.reload_doc();
			},
		});
	}
});

frappe.ui.form.on("Quoting Sheet Item", {
	item_code: function (frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (!row.item_code) {return;}
		frappe.call({
			method: "erpnext.selling.doctype.quoting_sheet.quoting_sheet.get_item_details_quoting_sheet",
			args: {
				"item_code": row.item_code
			},
			callback: (res) => {
				frappe.model.set_value(cdt, cdn, "rate", res.message.valuation_rate);
				frappe.model.set_value(cdt, cdn, "bom_no", res.message.default_bom);
				frappe.model.set_value(cdt, cdn, "uom", res.message.stock_uom);
			},
		});
	},

	qty: function (frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.qty && row.rate) {
			let amount = row.qty * row.rate;
			frappe.model.set_value(cdt, cdn, "amount", amount);
		}
	},

	rate: function (frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.qty && row.rate) {
			let amount = row.qty * row.rate;
			frappe.model.set_value(cdt, cdn, "amount", amount);
		}
	},

	customer_provided_item: function (frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.customer_provided_item) {
			frappe.model.set_value(cdt, cdn, "rate", 0.0);
			frappe.model.set_value(cdt, cdn, "amount", 0.0);
		}
		else {
			frappe.call({
				method: "erpnext.selling.doctype.quoting_sheet.quoting_sheet.get_item_details_quoting_sheet",
				args: {
					"item_code": row.item_code
				},
				callback: (res) => {
					frappe.model.set_value(cdt, cdn, "rate", res.message.valuation_rate);
					frm.trigger("qty", cdt, cdn);
				}
			});
		}
	}
});