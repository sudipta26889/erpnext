# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry
from erpnext.ai.tools import documents, reports


class TestReadTools(IntegrationTestCase):
	def setUp(self):
		self.company = frappe.db.get_value("Company", {}, "name")
		doc = frappe.get_single("AI Settings")
		doc.erpnext_company = self.company
		doc.max_batch_size = 5
		doc.save()

	def tearDown(self):
		frappe.db.rollback()

	def test_search_documents_returns_rows(self):
		out = documents.search_documents("Company", fields=["name"])
		self.assertTrue(any(row["name"] == self.company for row in out))

	def test_search_documents_clamps_limit(self):
		out = documents.search_documents("DocType", fields=["name"], limit=1000)
		self.assertLessEqual(len(out), 5)

	def test_search_documents_rejects_out_of_scope_company(self):
		with self.assertRaises(registry.ToolError):
			documents.search_documents("Sales Invoice", filters={"company": "Nope Ltd"})

	def test_search_documents_rejects_version_doctype(self):
		# Version stores field-level before/after diffs of arbitrary documents and
		# carries no `company` field of its own -- must be refused outright, not
		# silently let through scope_filters() as company-less-and-untouched.
		with self.assertRaises(registry.ToolError):
			documents.search_documents("Version", fields=["name"])

	def test_search_documents_rejects_deleted_document_doctype(self):
		# Deleted Document stores the full JSON of any deleted document, any
		# company included, with no `company` field to scope on.
		with self.assertRaises(registry.ToolError):
			documents.search_documents("Deleted Document", fields=["name"])

	def test_get_document_includes_child_tables(self):
		out = documents.get_document("Company", self.company)
		self.assertEqual(out["name"], self.company)
		self.assertEqual(out["doctype"], "Company")

	def test_get_missing_document_raises_tool_error(self):
		with self.assertRaises(registry.ToolError):
			documents.get_document("Company", "No Such Company Ltd")

	def test_get_document_rejects_version_doctype(self):
		with self.assertRaises(registry.ToolError):
			documents.get_document("Version", "irrelevant")

	def test_get_document_rejects_deleted_document_doctype(self):
		with self.assertRaises(registry.ToolError):
			documents.get_document("Deleted Document", "irrelevant")

	def test_run_report_rejects_unknown_report(self):
		with self.assertRaises(registry.ToolError):
			reports.run_report("Not A Report")


class TestMCPPermissionBoundary(IntegrationTestCase):
	"""A low-privilege user must be refused through tools/call, not just in the ORM."""

	def setUp(self):
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
		doc.save()

		self.user_email = "ai-lowpriv@example.com"
		if not frappe.db.exists("User", self.user_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": self.user_email,
					"first_name": "Low Priv",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_low_privilege_user_is_denied_through_tools_call(self):
		from erpnext.ai import mcp

		frappe.set_user(self.user_email)
		out = mcp.dispatch(
			{
				"jsonrpc": "2.0",
				"id": 1,
				"method": "tools/call",
				"params": {"name": "search_documents", "arguments": {"doctype": "GL Entry"}},
			}
		)
		self.assertTrue(out["result"]["isError"])
		text = out["result"]["content"][0]["text"]
		# The message must state refusal without revealing whether records exist.
		self.assertIn("Not permitted", text)
		self.assertNotIn("rows", text.lower())
