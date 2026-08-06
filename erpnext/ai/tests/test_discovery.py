# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry
from erpnext.ai.tools import discovery


class TestDiscovery(IntegrationTestCase):
	def setUp(self):
		doc = frappe.get_single("AI Settings")
		doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
		doc.save()

	def tearDown(self):
		frappe.db.rollback()

	def test_company_context_grounds_the_agent(self):
		out = discovery.get_company_context()
		self.assertEqual(out["company"], frappe.get_single("AI Settings").erpnext_company)
		self.assertTrue(out["currency"])
		self.assertIn("modules", out)

	def test_search_doctypes_finds_by_label(self):
		names = [d["doctype"] for d in discovery.search_doctypes("sales invoice")]
		self.assertIn("Sales Invoice", names)

	def test_search_doctypes_includes_desk_url(self):
		hit = next(d for d in discovery.search_doctypes("sales invoice") if d["doctype"] == "Sales Invoice")
		self.assertEqual(hit["url"], "/app/sales-invoice")

	def test_describe_doctype_reports_fields_and_permissions(self):
		out = discovery.describe_doctype("Sales Invoice")
		fieldnames = [f["fieldname"] for f in out["fields"]]
		self.assertIn("customer", fieldnames)
		self.assertTrue(out["is_submittable"])
		self.assertIn("read", out["permissions"])

	def test_describe_unknown_doctype_raises_tool_error(self):
		with self.assertRaises(registry.ToolError):
			discovery.describe_doctype("Not A Real Doctype")

	def test_list_reports_returns_entries(self):
		out = discovery.list_reports("Accounts")
		self.assertTrue(out)
		self.assertIn("name", out[0])

	def test_workspace_map_locates_doctypes(self):
		out = discovery.get_workspace_map()
		self.assertIn("Sales Invoice", out)
		self.assertIn("workspace", out["Sales Invoice"])
