# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry, scoping


class TestScoping(IntegrationTestCase):
	def setUp(self):
		self.company = frappe.db.get_value("Company", {}, "name")
		doc = frappe.get_single("AI Settings")
		doc.erpnext_company = self.company
		doc.additional_companies = []
		doc.save()

	def tearDown(self):
		frappe.db.rollback()

	def _make_second_company(self) -> str:
		"""Return a second Company, creating a throwaway one if the site only has one.

		ignore_chart_of_accounts skips Company.on_update()'s default-accounts
		and default-warehouses creation (the latter needs a "Warehouse Type:
		Transit" fixture this trimmed test site doesn't have) — irrelevant to
		what this test checks, and the whole insert is rolled back in tearDown.
		"""
		existing = frappe.db.get_value("Company", {"name": ["!=", self.company]}, "name")
		if existing:
			return existing
		previous_flag = frappe.local.flags.ignore_chart_of_accounts
		frappe.local.flags.ignore_chart_of_accounts = True
		try:
			doc = frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": "AI Scoping Test Co",
					"default_currency": "INR",
					"country": "India",
				}
			)
			doc.insert(ignore_permissions=True)
			return doc.name
		finally:
			frappe.local.flags.ignore_chart_of_accounts = previous_flag

	def test_bound_companies_defaults_to_one(self):
		self.assertEqual(scoping.bound_companies(), [self.company])

	def test_out_of_scope_company_is_refused_not_rescoped(self):
		with self.assertRaises(registry.ToolError):
			scoping.assert_company_allowed("Some Other Entity Ltd")

	def test_company_field_detection(self):
		self.assertTrue(scoping.has_company_field("Sales Invoice"))
		self.assertFalse(scoping.has_company_field("Currency"))

	def test_filters_get_company_injected(self):
		out = scoping.scope_filters("Sales Invoice", {"status": "Draft"})
		self.assertEqual(out["company"], self.company)
		self.assertEqual(out["status"], "Draft")

	def test_explicit_in_scope_company_is_preserved(self):
		out = scoping.scope_filters("Sales Invoice", {"company": self.company})
		self.assertEqual(out["company"], self.company)

	def test_explicit_out_of_scope_company_raises(self):
		with self.assertRaises(registry.ToolError):
			scoping.scope_filters("Sales Invoice", {"company": "Nope Ltd"})

	def test_non_company_doctype_is_untouched(self):
		out = scoping.scope_filters("Currency", {"enabled": 1})
		self.assertNotIn("company", out)

	def test_explicit_eq_operator_in_scope_is_preserved(self):
		out = scoping.scope_filters("Sales Invoice", {"company": ["=", self.company]})
		self.assertEqual(out["company"], ["=", self.company])

	def test_explicit_eq_operator_out_of_scope_raises(self):
		with self.assertRaises(registry.ToolError):
			scoping.scope_filters("Sales Invoice", {"company": ["=", "Nope Ltd"]})

	def test_explicit_in_operator_in_scope_is_preserved(self):
		out = scoping.scope_filters("Sales Invoice", {"company": ["in", [self.company]]})
		self.assertEqual(out["company"], ["in", [self.company]])

	def test_explicit_in_operator_with_one_out_of_scope_entry_raises(self):
		with self.assertRaises(registry.ToolError):
			scoping.scope_filters(
				"Sales Invoice", {"company": ["in", [self.company, "Some Other Entity Ltd"]]}
			)

	def test_explicit_not_equal_operator_raises_even_if_named_company_in_scope(self):
		# The named company is in scope, but `!=` selects everything else, which
		# is exactly what scoping must not allow.
		with self.assertRaises(registry.ToolError):
			scoping.scope_filters("Sales Invoice", {"company": ["!=", self.company]})

	def test_explicit_like_operator_raises(self):
		with self.assertRaises(registry.ToolError):
			scoping.scope_filters("Sales Invoice", {"company": ["like", "%Test%"]})

	def test_explicit_empty_in_operator_raises(self):
		with self.assertRaises(registry.ToolError):
			scoping.scope_filters("Sales Invoice", {"company": ["in", []]})

	def test_explicit_dict_company_filter_raises(self):
		with self.assertRaises(registry.ToolError):
			scoping.scope_filters("Sales Invoice", {"company": {"$ne": self.company}})

	def test_rejected_operator_filter_message_names_permitted_company(self):
		with self.assertRaises(registry.ToolError) as ctx:
			scoping.scope_filters("Sales Invoice", {"company": ["!=", self.company]})
		self.assertIn(self.company, str(ctx.exception))

	def test_explicit_uppercase_in_operator_is_accepted(self):
		# Frappe normalises filter operators case-insensitively, so ["IN", [...]]
		# is legal to Frappe; refusing it here was a false negative, not a hole
		# (the lowercase form was already accepted).
		out = scoping.scope_filters("Sales Invoice", {"company": ["IN", [self.company]]})
		self.assertEqual(out["company"], ["IN", [self.company]])

	def test_explicit_padded_in_operator_is_accepted(self):
		out = scoping.scope_filters("Sales Invoice", {"company": [" in ", [self.company]]})
		self.assertEqual(out["company"], [" in ", [self.company]])

	# -- IMPORTANT 5: scope_filters must not assume `filters` is a dict -----

	def test_scope_filters_rejects_three_element_list_form(self):
		# Frappe's list filter form; dict(filters or {}) would otherwise raise
		# a bare ValueError here, escaping this security function uncaught.
		with self.assertRaises(registry.ToolError):
			scoping.scope_filters("Sales Invoice", [["company", "=", "X"]])

	def test_scope_filters_rejects_two_element_list_form(self):
		# dict([["company", "Other Co"]]) silently coerces to
		# {"company": "Other Co"} — this must be refused, not swallowed.
		with self.assertRaises(registry.ToolError):
			scoping.scope_filters("Sales Invoice", [["company", "Other Co"]])

	def test_scope_filters_rejects_string_filters(self):
		with self.assertRaises(registry.ToolError):
			scoping.scope_filters("Sales Invoice", "company=X")

	# -- IMPORTANT 6: company-less doctypes that can leak other companies ---

	def test_assert_doctype_scopable_raises_for_version(self):
		with self.assertRaises(registry.ToolError):
			scoping.assert_doctype_scopable("Version")

	def test_assert_doctype_scopable_raises_for_comment(self):
		with self.assertRaises(registry.ToolError):
			scoping.assert_doctype_scopable("Comment")

	def test_assert_doctype_scopable_raises_for_deleted_document(self):
		# Stores the full JSON of any deleted document, any company included.
		with self.assertRaises(registry.ToolError):
			scoping.assert_doctype_scopable("Deleted Document")

	def test_assert_doctype_scopable_raises_for_prepared_report(self):
		# Stores rendered report output, which can embed any company's data.
		with self.assertRaises(registry.ToolError):
			scoping.assert_doctype_scopable("Prepared Report")

	def test_assert_doctype_scopable_passes_for_sales_invoice(self):
		scoping.assert_doctype_scopable("Sales Invoice")  # must not raise

	def test_assert_doctype_scopable_passes_for_item(self):
		scoping.assert_doctype_scopable("Item")  # must not raise

	# -- IMPORTANT 7: the multi-company path, actually exercised ------------

	def test_multi_company_scope_via_additional_companies(self):
		# Every other test in this file leaves additional_companies empty, so
		# bound_companies() never reads a real row.company off the Table
		# MultiSelect anywhere else — a field-name typo there would go
		# completely undetected without this.
		company_b = self._make_second_company()
		doc = frappe.get_single("AI Settings")
		doc.append("additional_companies", {"company": company_b})
		doc.save()

		self.assertEqual(set(scoping.bound_companies()), {self.company, company_b})

		out = scoping.scope_filters("Sales Invoice", {"company": ["in", [self.company, company_b]]})
		self.assertEqual(out["company"], ["in", [self.company, company_b]])

		with self.assertRaises(registry.ToolError):
			scoping.assert_company_allowed("Some Unbound Third Entity Ltd")

	# -- the multi-company guard: fail closed the instant it applies ---------

	def test_is_multi_company_site_false_with_one_company(self):
		self.assertFalse(scoping.is_multi_company_site())

	def test_is_multi_company_site_true_with_two_companies(self):
		self._make_second_company()
		self.assertTrue(scoping.is_multi_company_site())

	def test_assert_scopable_passes_for_company_field_doctype_on_single_company_site(self):
		scoping.assert_scopable("Sales Invoice")  # must not raise

	def test_assert_scopable_passes_for_company_field_doctype_on_multi_company_site(self):
		# Has a `company` field to scope on -- the guard is about doctypes that
		# don't, so this must keep working once a second Company exists.
		self._make_second_company()
		scoping.assert_scopable("Sales Invoice")  # must not raise

	def test_assert_scopable_passes_for_company_doctype_on_single_company_site(self):
		scoping.assert_scopable("Company")  # must not raise

	def test_assert_scopable_passes_for_company_doctype_on_multi_company_site(self):
		# Company carries no `company` field of its own -- it IS the entity,
		# scoped by identity (see documents.py), so it is always scopable and
		# must never be refused by this doctype-field-based guard.
		self._make_second_company()
		scoping.assert_scopable("Company")  # must not raise

	def test_assert_scopable_passes_for_company_less_doctype_on_single_company_site(self):
		# Nothing to leak between companies when there is only one.
		scoping.assert_scopable("Currency")  # must not raise

	def test_assert_scopable_raises_for_company_less_doctype_on_multi_company_site(self):
		self._make_second_company()
		with self.assertRaises(registry.ToolError):
			scoping.assert_scopable("Currency")
