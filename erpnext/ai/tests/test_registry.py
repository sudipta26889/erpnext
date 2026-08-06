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

	def test_value_exactly_at_cap_passes(self):
		doc = frappe.get_single("AI Settings")
		doc.max_document_value = 1000
		doc.save()
		registry.settings.cache_clear()
		# Current behaviour: the comparison is strictly-greater-than, so a value
		# exactly at the cap is not refused.
		registry.assert_value_within_cap(1000, "Sales Order")

	def test_none_value_raises_when_cap_configured(self):
		doc = frappe.get_single("AI Settings")
		doc.max_document_value = 1000
		doc.save()
		registry.settings.cache_clear()
		with self.assertRaises(registry.ToolError):
			registry.assert_value_within_cap(None, "Sales Order")

	def test_none_value_passes_when_cap_unlimited(self):
		doc = frappe.get_single("AI Settings")
		doc.max_document_value = 0
		doc.save()
		registry.settings.cache_clear()
		registry.assert_value_within_cap(None, "Sales Order")

	def test_clamp_limit_zero_returns_cap(self):
		doc = frappe.get_single("AI Settings")
		doc.max_batch_size = 9
		doc.save()
		registry.settings.cache_clear()
		self.assertEqual(registry.clamp_limit(0), 9)

	def test_clamp_limit_negative_returns_cap(self):
		doc = frappe.get_single("AI Settings")
		doc.max_batch_size = 9
		doc.save()
		registry.settings.cache_clear()
		self.assertEqual(registry.clamp_limit(-5), 9)

	def test_json_list_blank_is_genuinely_empty(self):
		self.assertEqual(registry._json_list("", "Enabled Tools"), [])
		self.assertEqual(registry._json_list(None, "Enabled Tools"), [])
		self.assertEqual(registry._json_list("   ", "Enabled Tools"), [])

	def test_json_list_valid_list_of_strings(self):
		self.assertEqual(registry._json_list('["ping", "pong"]', "Enabled Tools"), ["ping", "pong"])

	def test_json_list_string_scalar_raises_instead_of_exploding_into_chars(self):
		# Previously '"ping"' silently became ['p', 'i', 'n', 'g'].
		with self.assertRaises(registry.ToolError):
			registry._json_list('"ping"', "Enabled Tools")

	def test_json_list_number_scalar_raises(self):
		with self.assertRaises(registry.ToolError):
			registry._json_list("5", "Enabled Tools")

	def test_json_list_null_raises(self):
		with self.assertRaises(registry.ToolError):
			registry._json_list("null", "Enabled Tools")

	def test_json_list_dict_raises_instead_of_returning_keys(self):
		# Previously '{"a":"b"}' silently became ['a'].
		with self.assertRaises(registry.ToolError):
			registry._json_list('{"a":"b"}', "Enabled Tools")

	def test_json_list_non_string_items_raises(self):
		with self.assertRaises(registry.ToolError):
			registry._json_list("[1,2]", "Enabled Tools")

	def test_json_list_malformed_json_raises(self):
		with self.assertRaises(registry.ToolError):
			registry._json_list("[oops", "Enabled Tools")

	def test_json_list_error_names_the_offending_field(self):
		with self.assertRaises(registry.ToolError) as ctx:
			registry._json_list("5", "Enabled Tools")
		self.assertIn("Enabled Tools", str(ctx.exception))

	def test_saving_ai_settings_invalidates_cache_without_explicit_clear(self):
		# Populate the cache with the current value first.
		before = registry.settings().max_batch_size or 20
		doc = frappe.get_single("AI Settings")
		doc.max_batch_size = before + 1
		doc.save()
		# Deliberately no registry.settings.cache_clear() here: the on_update
		# hook on AISettings must invalidate the lru_cache on its own.
		self.assertEqual(registry.settings().max_batch_size, before + 1)
