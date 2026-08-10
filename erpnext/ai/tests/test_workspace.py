# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestAIWorkspace(IntegrationTestCase):
	def test_workspace_sits_between_home_and_invoicing(self):
		ai = frappe.db.get_value("Workspace", "AI", ["sequence_id", "public"], as_dict=True)
		home = frappe.db.get_value("Workspace", "Home", "sequence_id")
		invoicing = frappe.db.get_value("Workspace", "Invoicing", "sequence_id")
		self.assertTrue(home < ai.sequence_id < invoicing)
		self.assertTrue(ai.public)

	def test_the_first_sidebar_item_is_the_chat_page(self):
		# v17 resolves a rail entry to the FIRST Link in the workspace's authored
		# sidebar_items (frappe/boot.py::get_sidebar_items). With none authored, the
		# boot falls back to a sidebar generated from the module whose first link is
		# the workspace itself -- which renders the workspace's empty content, i.e. a
		# blank page. That is exactly what shipped first.
		first = frappe.get_doc("Workspace", "AI").sidebar_items[0]
		self.assertEqual(first.type, "Link")
		self.assertEqual(first.link_type, "Page")
		self.assertEqual(first.link_to, "ai-chat")

	def test_the_page_route_is_not_shadowed_by_a_workspace(self):
		# router.js::convert_to_standard_route checks frappe.workspaces[route[0]]
		# BEFORE falling through to pageview, so a Page sharing a workspace's slug is
		# unreachable: /desk/ai always renders the workspace. The page must therefore
		# never be named after the workspace.
		self.assertTrue(frappe.db.exists("Page", "ai-chat"))
		self.assertFalse(frappe.db.exists("Workspace", "ai-chat"))
		# and nothing may reclaim the shadowed name
		self.assertFalse(frappe.db.exists("Page", "ai"))
