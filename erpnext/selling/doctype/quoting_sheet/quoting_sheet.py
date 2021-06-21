# -*- coding: utf-8 -*-
# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import flt
from frappe.model.document import Document

class QuotingSheet(Document):
	def validate(self):
		self.calculate_single_raw_material_cost()
		self.calculate_total_raw_material_cost()
		self.calculate_totals()

	def calculate_totals(self):
		"""
			Calculates total cost and cost per unit of item
		"""
		total_charges = self.rm_cost + self.packaging_charges + self.shipping_cost
		self.total_price = total_charges + ((total_charges * int(self.profit_markup))/100)
		 
		self.price_per_unit = self.total_price / self.qty

	def calculate_total_raw_material_cost(self):
		self.rm_cost = 0
		for item in self.raw_material_items:
			self.rm_cost += item.amount

	def get_raw_materials(self):
		# raw_materials_list = []
		self.raw_material_items = []
		raw_materials = frappe.get_all("BOM Item", filters={"parent": self.bom})
		for material in raw_materials:
			bom_items = frappe.db.get_value("BOM Item", material.name, ["item_code", "qty", "rate", "uom", "item_name"], as_dict = 1 )
			if bom_items:
				self.append("raw_material_items", {
					"item_code": bom_items.get("item_code"),
					"qty": bom_items.get("qty"),
					"rate": bom_items.get("rate"),
					"uom": bom_items.get("uom"),
					"item_name": bom_items.get("item_name")
				})
		self.calculate_single_raw_material_cost()
		self.rm_cost = 0
		# self.calculate_total_raw_material_cost()
		# 	raw_materials_list.append(frappe.db.get_value("BOM Item", material.name, ["item_code", "qty", "rate", "uom", "item_name"], as_dict = 1 ))
		# print("RAW MATERIAL============", raw_materials_list)
		# self.raw_materials_items = raw_materials_list
	def calculate_single_raw_material_cost(self):
		for item in self.raw_material_items:
			item.amount = flt(item.qty) * flt(item.rate)


@frappe.whitelist()
def get_item_details_quoting_sheet(item_code):
	"""
	Send valuation rate, stock UOM and default BOM name to quoting sheet

	Args:
		item_code (str): item code for sending details to raw materials table in quoting sheet

	Returns:
		dict: return valuation rate, stock UOM and default BOM
	"""	
	return frappe.db.get_values("Item", item_code, ["valuation_rate", "stock_uom", "default_bom"], as_dict=1)[0]

@frappe.whitelist()
def update_latest_rate(docname):
	"""
	get latest value of valuation_rate from db and update it in Quoting Sheet
	"""	
	doc = frappe.get_doc("Quoting Sheet", docname)
	for item in doc.raw_material_items:
		rate = frappe.db.get_value("Item", item.get("item_code"), "valuation_rate", as_dict=1)
		item.rate = rate.valuation_rate
		item.amount = rate.valuation_rate * item.qty
	doc.save()
	frappe.msgprint(_("Rate updated"))

	
