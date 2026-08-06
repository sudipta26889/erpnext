# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry, scoping


class TestScoping(IntegrationTestCase):
	def setUp(self):
		registry.settings.cache_clear()
		self.company = frappe.db.get_value("Company", {}, "name")
		doc = frappe.get_single("AI Settings")
		doc.erpnext_company = self.company
		doc.additional_companies = []
		doc.save()
		registry.settings.cache_clear()

	def tearDown(self):
		registry.settings.cache_clear()
		frappe.db.rollback()

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
