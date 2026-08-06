# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry


class TestRegistry(IntegrationTestCase):
	def setUp(self):
		registry.settings.cache_clear()

	def tearDown(self):
		registry.settings.cache_clear()
		frappe.db.rollback()

	def test_registered_tool_is_returned(self):
		names = [t.name for t in registry.get_tools()]
		self.assertIn("ping", names)

	def test_unknown_tool_raises(self):
		with self.assertRaises(registry.ToolError):
			registry.get_tool("no_such_tool")

	def test_enabled_tools_allowlist_hides_others(self):
		doc = frappe.get_single("AI Settings")
		doc.enabled_tools = '["ping"]'
		doc.save()
		registry.settings.cache_clear()
		self.assertEqual([t.name for t in registry.get_tools()], ["ping"])

	def test_limit_is_clamped_to_max_batch_size(self):
		doc = frappe.get_single("AI Settings")
		doc.max_batch_size = 5
		doc.save()
		registry.settings.cache_clear()
		self.assertEqual(registry.clamp_limit(100), 5)
		self.assertEqual(registry.clamp_limit(None), 5)
		self.assertEqual(registry.clamp_limit(2), 2)

	def test_value_cap_refuses_rather_than_truncating(self):
		doc = frappe.get_single("AI Settings")
		doc.max_document_value = 1000
		doc.save()
		registry.settings.cache_clear()
		registry.assert_value_within_cap(999, "Sales Order")
		with self.assertRaises(registry.ToolError):
			registry.assert_value_within_cap(1001, "Sales Order")

	def test_zero_value_cap_means_unlimited(self):
		doc = frappe.get_single("AI Settings")
		doc.max_document_value = 0
		doc.save()
		registry.settings.cache_clear()
		registry.assert_value_within_cap(10**9, "Sales Order")
