# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe
from frappe import throw, _
import frappe.defaults
from frappe.utils import cint, flt, get_fullname, cstr
from frappe.contacts.doctype.address.address import get_address_display
from erpnext.shopping_cart.doctype.shopping_cart_settings.shopping_cart_settings import get_shopping_cart_settings, get_default_payment_gateway_account
from frappe.utils.nestedset import get_root_of
from erpnext.accounts.utils import get_account_name
from erpnext.utilities.product import get_qty_in_stock
from frappe.contacts.doctype.contact.contact import get_contact_name
from frappe.utils import cint, cstr, getdate, nowdate


class WebsitePriceListMissingError(frappe.ValidationError):
	pass

def set_cart_count(quotation=None):
	if cint(frappe.db.get_singles_value("Shopping Cart Settings", "enabled")):
		cart_count = 0
		if not quotation:
			quotation = _get_cart_quotation()

		for item in quotation.get("items"):
			cart_count += cint(item.get("qty"))

		if hasattr(frappe.local, "cookie_manager"):
			frappe.local.cookie_manager.set_cookie("cart_count", cstr(cart_count))

@frappe.whitelist()
def get_cart_quotation(doc=None):
	party = get_party()

	if not doc:
		quotation = _get_cart_quotation(party)
		doc = quotation
		set_cart_count(quotation)

	addresses = get_address_docs(party=party)

	if not doc.customer_address and addresses:
		update_cart_address("billing", addresses[0].name)

	return {
		"doc": decorate_quotation_doc(doc),
		"shipping_addresses": [{"name": address.name, "display": address.display}
			for address in addresses if address.address_type == "Shipping"],
		"billing_addresses": [{"name": address.name, "display": address.display}
			for address in addresses if address.address_type == "Billing"],
		"shipping_rules": get_applicable_shipping_rules(party),
		"cart_settings": frappe.get_cached_doc("Shopping Cart Settings")
	}

@frappe.whitelist()
def place_order(delivery_date=None):
	"""
	Place an order for items in the shopping cart.

	Args:
		delivery_date (date, optional): The delivery date requested by the sales user/customer. Defaults to None.

	Returns:
		string: the name of the quotation or the sales order
	"""
	#get the quotation in the cart and the cart settings
	quotation = _get_cart_quotation()
	cart_settings = frappe.db.get_value("Shopping Cart Settings", None,
		["company", "allow_items_not_in_stock", 'sales_team_order_without_payment'], as_dict=1)
	quotation.company = cart_settings.company
	quotation.flags.ignore_permissions = True

	if quotation.quotation_to == 'Lead' and quotation.party_name:
		# company used to create customer accounts
		frappe.defaults.set_user_default("company", quotation.company)

	if not (quotation.shipping_address_name or quotation.customer_address):
		frappe.throw(_("Set Shipping Address or Billing Address"))

	# Checking if items in quotation are in stock, if not throw an error
	if not cint(cart_settings.allow_items_not_in_stock):
		for item in quotation.get("items"):
			item.reserved_warehouse, is_stock_item = frappe.db.get_value("Item",
				item.item_code, ["website_warehouse", "is_stock_item"])

			if is_stock_item:
				item_stock = get_qty_in_stock(item.item_code, "website_warehouse")
				if not cint(item_stock.in_stock):
					throw(_("{1} Not in Stock").format(item.item_code))
				if item.qty > item_stock.stock_qty[0][0]:
					throw(_("Only {0} in Stock for item {1}: {2}").format(item_stock.stock_qty[0][0], item.item_code, item.item_name))

	#if checkout without payment has been enabled, submit the quotation, and convert to sales order
	if cart_settings.sales_team_order_without_payment:

		#add the requested delivery date to the quotation and submit the document
		quotation.requested_delivery_date = getdate(delivery_date)
		for item in quotation.items:
			item.requested_delivery_date = getdate(delivery_date)

		quotation.save(ignore_permissions=True)
		quotation.submit()
		frappe.db.commit()

		#convert the quotation to a sales order
		from erpnext.selling.doctype.quotation.quotation import _make_sales_order
		sales_order = frappe.get_doc(_make_sales_order(quotation.name, ignore_permissions=True))
		sales_order.payment_schedule = []
		sales_order.transaction_date = nowdate()
		#expected delivery date to the sales order is mapped from the quotation

		sales_order.flags.ignore_mandatory = True
		sales_order.flags.ignore_links = True
		sales_order.insert(ignore_permissions=True)
		sales_order.submit()

		return sales_order.name

	return quotation.name

@frappe.whitelist()
def request_for_quotation():
	quotation = _get_cart_quotation()
	quotation.flags.ignore_permissions = True
	quotation.submit()
	return quotation.name

@frappe.whitelist()
def update_cart(item_code, qty, batch_no=None, row=None, additional_notes=None, with_items=False):
	quotation = _get_cart_quotation()

	empty_card = False
	qty = flt(qty)
	if qty == 0:
		if row is None:
			quotation_items = quotation.get("items", {"item_code": ["!=", item_code]})
		else:
			quotation_items = quotation.get("items", {"name": ["!=", row]})

		if quotation_items:
			quotation.set("items", quotation_items)
		else:
			empty_card = True
	else:
		quotation_items = quotation.get("items", {"item_code": item_code})
		if not quotation_items:
			quotation.append("items", {
				"doctype": "Quotation Item",
				"item_code": item_code,
				"qty": qty,
				"batch_no": batch_no,
				"additional_notes": additional_notes
			})
		else:
			quotation_items[0].qty = qty
			quotation_items[0].additional_notes = additional_notes

	apply_cart_settings(quotation=quotation)

	quotation.flags.ignore_permissions = True
	quotation.payment_schedule = []
	if not empty_card:
		quotation.save()
	else:
		quotation.delete()
		quotation = None

	set_cart_count(quotation)

	context = get_cart_quotation(quotation)

	if cint(with_items):
		return {
			"items": frappe.render_template("templates/includes/cart/cart_items.html",
				context),
			"taxes": frappe.render_template("templates/includes/order/order_taxes.html",
				context),
		}
	else:
		return {
			'name': quotation.name,
			'shopping_cart_menu': get_shopping_cart_menu(context)
		}

@frappe.whitelist()
def get_shopping_cart_menu(context=None):
	if not context:
		context = get_cart_quotation()

	return frappe.render_template('templates/includes/cart/cart_dropdown.html', context)


@frappe.whitelist()
def add_new_address(doc):
	doc = frappe.parse_json(doc)
	doc.update({
		'doctype': 'Address'
	})
	address = frappe.get_doc(doc)
	address.save(ignore_permissions=True)

	return address

@frappe.whitelist(allow_guest=True)
def create_lead_for_item_inquiry(lead, subject, message):
	lead = frappe.parse_json(lead)
	lead_doc = frappe.new_doc('Lead')
	lead_doc.update(lead)
	lead_doc.set('lead_owner', '')

	try:
		lead_doc.save(ignore_permissions=True)
	except frappe.exceptions.DuplicateEntryError:
		frappe.clear_messages()
		lead_doc = frappe.get_doc('Lead', {'email_id': lead['email_id']})

	lead_doc.add_comment('Comment', text='''
		<div>
			<h5>{subject}</h5>
			<p>{message}</p>
		</div>
	'''.format(subject=subject, message=message))

	return lead_doc


@frappe.whitelist()
def get_terms_and_conditions(terms_name):
	return frappe.db.get_value('Terms and Conditions', terms_name, 'terms')

@frappe.whitelist()
def update_cart_address(address_type, address_name):
	quotation = _get_cart_quotation()
	address_display = get_address_display(frappe.get_doc("Address", address_name).as_dict())

	if address_type.lower() == "billing":
		quotation.customer_address = address_name
		quotation.address_display = address_display
		quotation.shipping_address_name == quotation.shipping_address_name or address_name
	elif address_type.lower() == "shipping":
		quotation.shipping_address_name = address_name
		quotation.shipping_address = address_display
		quotation.customer_address == quotation.customer_address or address_name

	apply_cart_settings(quotation=quotation)

	quotation.flags.ignore_permissions = True
	quotation.save()

	context = get_cart_quotation(quotation)
	return {
		"taxes": frappe.render_template("templates/includes/order/order_taxes.html",
			context),
		}

def guess_territory():
	territory = None
	geoip_country = frappe.session.get("session_country")
	if geoip_country:
		territory = frappe.db.get_value("Territory", geoip_country)

	return territory or \
		frappe.db.get_value("Shopping Cart Settings", None, "territory") or \
			get_root_of("Territory")

def decorate_quotation_doc(doc):
	for d in doc.get("items", []):
		d.update(frappe.db.get_value("Item", d.item_code,
			["thumbnail", "website_image", "description", "route"], as_dict=True))

	return doc


def _get_cart_quotation(party=None):
	'''Return the open Quotation of type "Shopping Cart" or make a new one'''
	if not party:
		party = get_party()

	quotation = frappe.get_all("Quotation", fields=["name"], filters=
		{"party_name": party.name, "order_type": "Shopping Cart", "docstatus": 0},
		order_by="modified desc", limit_page_length=1)

	if quotation:
		qdoc = frappe.get_doc("Quotation", quotation[0].name)
	else:
		company = frappe.db.get_value("Shopping Cart Settings", None, ["company"])
		qdoc = frappe.get_doc({
			"doctype": "Quotation",
			"naming_series": get_shopping_cart_settings().quotation_series or "QTN-CART-",
			"quotation_to": party.doctype,
			"company": company,
			"order_type": "Shopping Cart",
			"status": "Draft",
			"docstatus": 0,
			"__islocal": 1,
			"party_name": party.name
		})

		contact_email_parent = frappe.db.get_value("Contact Email", {"email_id": frappe.session.user}, ["parent"])
		contacts = frappe.db.get_value("Contact", contact_email_parent, ["name", "title"], as_dict=True)
		if contacts:
			qdoc.contact_person = contacts.name
			qdoc.contact_display = contacts.title
			qdoc.contact_email = frappe.session.user

		qdoc.flags.ignore_permissions = True

		# Order For feature:
		if "order_for" in frappe.session.data:
			customer_name = frappe.session.data.order_for.get("customer_name")

			if customer_name:
				# override contact person
				primary_contact = frappe.session.data.order_for.get("customer_primary_contact_name")

				qdoc.contact_person = primary_contact
				qdoc.contact_email = frappe.get_value("Contact", primary_contact, "email_id")

				# override customer
				qdoc.party_name = customer_name

		# Hook: Allows overriding cart quotation creation for shopping cart
		#
		# Signature:
		#       override_shopping_cart_get_quotation(party, qdoc)
		#
		# Args:
		#		party: A doctype object(ex: Customer)
		#		qdoc: Quotation doctype
		hooks = frappe.get_hooks("override_shopping_cart_get_quotation") or []
		for method in hooks:
			frappe.call(method, party=party, qdoc=qdoc)

		qdoc.run_method("set_missing_values")
		apply_cart_settings(party, qdoc)

	return qdoc

def update_party(fullname, company_name=None, mobile_no=None, phone=None):
	party = get_party()

	party.customer_name = company_name or fullname
	party.customer_type == "Company" if company_name else "Individual"

	contact_name = frappe.db.get_value("Contact", {"email_id": frappe.session.user})
	contact = frappe.get_doc("Contact", contact_name)
	contact.first_name = fullname
	contact.last_name = None
	contact.customer_name = party.customer_name
	contact.mobile_no = mobile_no
	contact.phone = phone
	contact.flags.ignore_permissions = True
	contact.save()

	party_doc = frappe.get_doc(party.as_dict())
	party_doc.flags.ignore_permissions = True
	party_doc.save()

	qdoc = _get_cart_quotation(party)
	if not qdoc.get("__islocal"):
		qdoc.customer_name = company_name or fullname
		qdoc.run_method("set_missing_lead_customer_details")
		qdoc.flags.ignore_permissions = True
		qdoc.save()

def apply_cart_settings(party=None, quotation=None):
	if not party:
		party = get_party()
	if not quotation:
		quotation = _get_cart_quotation(party)

	cart_settings = frappe.get_doc("Shopping Cart Settings")

	set_price_list_and_rate(quotation, cart_settings)

	quotation.run_method("calculate_taxes_and_totals")

	set_taxes(quotation, cart_settings)

	_apply_shipping_rule(party, quotation, cart_settings)

def set_price_list_and_rate(quotation, cart_settings):
	"""set price list based on billing territory"""

	_set_price_list(cart_settings, quotation)

	# reset values
	quotation.price_list_currency = quotation.currency = \
		quotation.plc_conversion_rate = quotation.conversion_rate = None
	for item in quotation.get("items"):
		item.price_list_rate = item.rate = item.amount = None

	# refetch values
	quotation.run_method("set_price_list_and_item_details")

	if hasattr(frappe.local, "cookie_manager"):
		# set it in cookies for using in product page
		frappe.local.cookie_manager.set_cookie("selling_price_list", quotation.selling_price_list)

def _set_price_list(cart_settings, quotation=None):
	"""Set price list based on customer or shopping cart default"""
	from erpnext.accounts.party import get_default_price_list
	party_name = quotation.get("party_name") if quotation else get_party().get("name")
	selling_price_list = None

	# check if default customer price list exists
	if party_name:
		selling_price_list = get_default_price_list(frappe.get_doc("Customer", party_name))

	# check default price list in shopping cart
	if not selling_price_list:
		selling_price_list = cart_settings.price_list

	if quotation:
		quotation.selling_price_list = selling_price_list

	return selling_price_list

def set_taxes(quotation, cart_settings):
	"""set taxes based on billing territory"""
	from erpnext.accounts.party import set_taxes

	customer_group = frappe.db.get_value("Customer", quotation.party_name, "customer_group")

	quotation.taxes_and_charges = set_taxes(quotation.party_name, "Customer",
		quotation.transaction_date, quotation.company, customer_group=customer_group, supplier_group=None,
		tax_category=quotation.tax_category, billing_address=quotation.customer_address,
		shipping_address=quotation.shipping_address_name, use_for_shopping_cart=1)
#
# 	# clear table
	quotation.set("taxes", [])
#
# 	# append taxes
	quotation.append_taxes_from_master()

def get_party(user=None):
	if not user:
		user = frappe.session.user

	contact_name = get_contact_name(user)
	party = None

	# Order For feature. Sets current customer's primary contact for this session.
	if "order_for" in frappe.session.data:
		customer_name = frappe.session.data.order_for.get("customer_name")

		if customer_name and "customer_primary_contact_name" in frappe.session.data.order_for:
			contact_name = frappe.session.data.order_for.customer_primary_contact_name

	# Hook: Allows overriding the contact person used by the shopping cart
	#
	# Signature:
	#		override_shopping_cart_get_party_contact(contact_name)
	#
	# Args:
	#		contact_name: The contact name that will be used to fetch a party
	#
	# Returns:
	#		Hook expects a string or None to override the contact_name

	hooks = frappe.get_hooks("override_shopping_cart_get_party_contact") or []
	for method in hooks:
		contact_name = frappe.call(method, contact_name=contact_name) or contact_name

	if contact_name:
		contact = frappe.get_doc('Contact', contact_name)
		if contact.links:
			party_doctype, party = _get_customer_or_lead(contact)

	cart_settings = frappe.get_doc("Shopping Cart Settings")

	debtors_account = ''

	if cart_settings.enable_checkout:
		debtors_account = get_debtors_account(cart_settings)

	if party:
		return frappe.get_doc(party_doctype, party)

	else:
		if not cart_settings.enabled:
			frappe.local.flags.redirect_location = "/contact"
			raise frappe.Redirect

		fullname = get_fullname(user)
		contact = frappe.db.get_value("Contact Email", {"email_id": user}, "parent")

		customer = frappe.new_doc("Customer")
		customer.update({
			"customer_name": fullname,
			"customer_type": "Individual",
			"customer_group": get_shopping_cart_settings().default_customer_group,
			"territory": get_root_of("Territory"),
			"customer_primary_contact": contact
		})

		if debtors_account:
			customer.update({
				"accounts": [{
					"company": cart_settings.company,
					"account": debtors_account
				}]
			})

		customer.customer_primary_contact = contact
		customer.flags.ignore_mandatory = True
		customer.save(ignore_permissions=True)

		if not contact:
			contact = frappe.new_doc("Contact")
			contact.update({
				"first_name": fullname,
				"email_ids": [{"email_id": user, "is_primary": 1}]
			})
		else:
			contact = frappe.get_doc("Contact", contact)

		contact.append('links', dict(link_doctype='Customer', link_name=customer.name))
		contact.flags.ignore_mandatory = True
		contact.save(ignore_permissions=True)

		if not customer.customer_primary_contact:
			customer.customer_primary_contact = contact.name

		return customer

def _get_customer_or_lead(contact):
	if contact.get("links", {"link_doctype": "Customer"}):
		links = contact.get("links", {"link_doctype": "Customer"})[0]
		return links.link_doctype, links.link_name
	elif contact.get("links", {"link_doctype": "Lead"}):
		links = contact.get("links", {"link_doctype": "Lead"})[0]
		return links.link_doctype, links.link_name
	else:
		return contact.links[0].link_doctype, contact.links[0].link_name

def get_debtors_account(cart_settings):
	payment_gateway_account = get_default_payment_gateway_account(cart_settings)
	payment_gateway_account_currency = frappe.get_doc("Payment Gateway Account", payment_gateway_account).currency

	account_name = _("Debtors ({0})".format(payment_gateway_account_currency))

	debtors_account_name = get_account_name("Receivable", "Asset", is_group=0,\
		account_currency=payment_gateway_account_currency, company=cart_settings.company)

	if not debtors_account_name:
		debtors_account = frappe.get_doc({
			"doctype": "Account",
			"account_type": "Receivable",
			"root_type": "Asset",
			"is_group": 0,
			"parent_account": get_account_name(root_type="Asset", is_group=1, company=cart_settings.company),
			"account_name": account_name,
			"currency": payment_gateway_account_currency
		}).insert(ignore_permissions=True)

		return debtors_account.name

	else:
		return debtors_account_name


def get_address_docs(doctype=None, txt=None, filters=None, limit_start=0, limit_page_length=20,
	party=None):
	if not party:
		party = get_party()

	if not party:
		return []

	address_names = frappe.get_all("Address", filters=[
		["Address", "disabled", "=", 0],
		["Dynamic Link", "link_doctype", "=", party.doctype],
		["Dynamic Link", "link_name", "=", party.name],
		["Dynamic Link", "parenttype", "=", "Address"],
	])

	out = []

	for a in address_names:
		address = frappe.get_doc('Address', a.name)
		address.display = get_address_display(address.as_dict())
		out.append(address)

	return out

@frappe.whitelist()
def apply_shipping_rule(shipping_rule):
	quotation = _get_cart_quotation()

	quotation.shipping_rule = shipping_rule

	apply_cart_settings(quotation=quotation)

	quotation.flags.ignore_permissions = True
	quotation.save()

	return get_cart_quotation(quotation)

def _apply_shipping_rule(party=None, quotation=None, cart_settings=None):
	if not quotation.shipping_rule:
		shipping_rules = get_shipping_rules(quotation, cart_settings)

		if not shipping_rules:
			return

		elif quotation.shipping_rule not in shipping_rules:
			quotation.shipping_rule = shipping_rules[0]

	if quotation.shipping_rule:
		quotation.run_method("apply_shipping_rule")
		quotation.run_method("calculate_taxes_and_totals")

def get_applicable_shipping_rules(party=None, quotation=None):
	shipping_rules = get_shipping_rules(quotation)

	if shipping_rules:
		rule_label_map = frappe.db.get_values("Shipping Rule", shipping_rules, "label")
		# we need this in sorted order as per the position of the rule in the settings page
		return [[rule, rule] for rule in shipping_rules]

def get_shipping_rules(quotation=None, cart_settings=None):
	if not quotation:
		quotation = _get_cart_quotation()

	shipping_rules = []
	if quotation.shipping_address_name:
		country = frappe.db.get_value("Address", quotation.shipping_address_name, "country")
		if country:
			shipping_rules = frappe.db.sql_list("""select distinct sr.name
				from `tabShipping Rule Country` src, `tabShipping Rule` sr
				where src.country = %s and
				sr.disabled != 1 and sr.name = src.parent""", country)

	return shipping_rules

def get_address_territory(address_name):
	"""Tries to match city, state and country of address to existing territory"""
	territory = None

	if address_name:
		address_fields = frappe.db.get_value("Address", address_name,
			["city", "state", "country"])
		for value in address_fields:
			territory = frappe.db.get_value("Territory", value)
			if territory:
				break

	return territory

def show_terms(doc):
	return doc.tc_name

@frappe.whitelist(allow_guest=True)
def apply_coupon_code(applied_code, applied_referral_sales_partner):
	quotation = True

	if not applied_code:
		frappe.throw(_("Please enter a coupon code"), title=_("Coupon Error"))

	coupon_list = frappe.get_all('Coupon Code', filters={'coupon_code': applied_code})
	if not coupon_list:
		frappe.throw(_("Please enter a valid coupon code"), title=_("Coupon Error"))

	coupon_name = coupon_list[0].name

	from erpnext.accounts.doctype.pricing_rule.utils import validate_coupon_code
	validate_coupon_code(coupon_name)
	quotation = _get_cart_quotation()

	if quotation.coupon_code and quotation.coupon_code == coupon_name:
		frappe.throw(_("Coupon Code '{0}' already applied").format(coupon_name), title=_("Coupon Error"))

	quotation.coupon_code = coupon_name
	quotation.flags.ignore_permissions = True
	quotation.save()

	if applied_referral_sales_partner:
		sales_partner_list = frappe.get_all('Sales Partner', filters={'referral_code': applied_referral_sales_partner})
		if sales_partner_list:
			sales_partner_name = sales_partner_list[0].name
			quotation.referral_sales_partner = sales_partner_name
			quotation.flags.ignore_permissions = True
			quotation.save()

	return quotation
