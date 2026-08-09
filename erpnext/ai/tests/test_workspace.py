# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestAIWorkspace(IntegrationTestCase):
	def test_workspace_sits_between_home_and_invoicing(self):
		ai = frappe.db.get_value("Workspace", "AI", ["sequence_id", "public", "type"], as_dict=True)
		home = frappe.db.get_value("Workspace", "Home", "sequence_id")
		invoicing = frappe.db.get_value("Workspace", "Invoicing", "sequence_id")
		self.assertTrue(home < ai.sequence_id < invoicing)
		self.assertTrue(ai.public)

	def test_workspace_links_to_the_ai_page(self):
		ws = frappe.get_doc("Workspace", "AI")
		self.assertEqual(ws.type, "Link")
		self.assertEqual(ws.link_type, "Page")
		self.assertEqual(ws.link_to, "ai")

	def test_page_exists(self):
		self.assertTrue(frappe.db.exists("Page", "ai"))
