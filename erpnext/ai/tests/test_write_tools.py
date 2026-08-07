# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry
from erpnext.ai.tools import documents, methods


class TestWriteTools(IntegrationTestCase):
	def setUp(self):
		self.company = frappe.db.get_value("Company", {}, "name")
		doc = frappe.get_single("AI Settings")
		doc.erpnext_company = self.company
		doc.additional_companies = []
		doc.max_document_value = 0
		doc.allowed_methods = '["erpnext.ai.tools.methods.echo"]'
		doc.save()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _make_second_company(self) -> str:
		"""Return a second Company, creating a throwaway one if the site only has one.

		Mirrors TestReadTools._make_second_company (test_read_tools.py).
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
					"company_name": "AI Write Tools Test Co",
					"default_currency": "INR",
					"country": "India",
				}
			)
			doc.insert(ignore_permissions=True)
			return doc.name
		finally:
			frappe.local.flags.ignore_chart_of_accounts = previous_flag

	# -- create_document ---------------------------------------------------

	def test_create_document_makes_a_draft(self):
		out = documents.create_document("Note", {"title": "AI test note"}, idempotency_key="k-create-1")
		self.assertEqual(out["doctype"], "Note")
		self.assertEqual(out["docstatus"], 0)
		self.assertFalse(out["reused"])

	def test_repeat_idempotency_key_returns_the_original(self):
		first = documents.create_document("Note", {"title": "once"}, idempotency_key="k-dup")
		second = documents.create_document("Note", {"title": "once"}, idempotency_key="k-dup")
		self.assertEqual(first["name"], second["name"])
		self.assertTrue(second["reused"])

	def test_create_requires_idempotency_key(self):
		with self.assertRaises(TypeError):
			documents.create_document("Note", {"title": "no key"})

	def test_create_document_rejects_unscopable_doctype(self):
		# Same deny-list search_documents/get_document enforce (scoping.
		# assert_doctype_scopable) -- write tools must check it before
		# touching any data too.
		with self.assertRaises(registry.ToolError):
			documents.create_document("Version", {}, idempotency_key="k-version")

	def test_create_document_rejects_company(self):
		# Company is identity-scoped for reads (bound_companies()), but that's
		# a read-side guarantee only -- mutating the set of legal entities is
		# out of scope for this tool surface. See documents._assert_writable_doctype.
		with self.assertRaises(registry.ToolError):
			documents.create_document(
				"Company",
				{"company_name": "Rogue Co", "default_currency": "USD", "country": "India"},
				idempotency_key="k-company",
			)

	# -- update_document ------------------------------------------------

	def test_update_rejects_submitted_documents(self):
		note = frappe.get_doc({"doctype": "Note", "title": "immutable"}).insert()
		# Simulate a submitted doc; update_document must refuse regardless of doctype.
		frappe.db.set_value("Note", note.name, "docstatus", 1)
		with self.assertRaises(registry.ToolError):
			documents.update_document("Note", note.name, {"title": "changed"})

	def test_update_document_rejects_company(self):
		with self.assertRaises(registry.ToolError):
			documents.update_document("Company", self.company, {"company_name": "Renamed"})

	def test_update_document_rejects_out_of_scope_company_document(self):
		other_company = self._make_second_company()
		ts = frappe.get_doc(
			{"doctype": "Timesheet", "company": other_company, "time_logs": [{"is_billable": 0}]}
		)
		ts.insert(ignore_permissions=True)

		# frappe.PermissionError, not registry.ToolError: this is the write-tool
		# counterpart of get_document's post-read company check (test_read_tools.py) --
		# by now the caller already knows write permission on this doc would
		# otherwise be granted, so a distinguishable "wrong company" ToolError
		# would still leak that the record exists in some other scope.
		with self.assertRaises(frappe.PermissionError):
			documents.update_document("Timesheet", ts.name, {"note": "x"})

	# -- delete_document ------------------------------------------------

	def test_delete_removes_the_document(self):
		note = frappe.get_doc({"doctype": "Note", "title": "to delete"}).insert()
		out = documents.delete_document("Note", note.name)
		self.assertTrue(out["deleted"])
		self.assertFalse(frappe.db.exists("Note", note.name))

	def test_delete_of_missing_document_raises_does_not_exist_error(self):
		# Not registry.ToolError: a distinguishable message here would be an
		# existence oracle, exactly like get_document's equivalent case
		# (test_read_tools.py). frappe.get_doc()'s DoesNotExistError is what
		# mcp.py's tools/call handler collapses into the same uniform message
		# as an existing-but-forbidden record -- see
		# TestMCPWriteToolsPermissionBoundary below.
		with self.assertRaises(frappe.DoesNotExistError):
			documents.delete_document("Note", "no-such-note")

	def test_delete_document_rejects_company(self):
		with self.assertRaises(registry.ToolError):
			documents.delete_document("Company", self.company)

	# -- call_method ------------------------------------------------------

	def test_call_method_refuses_unlisted_paths(self):
		with self.assertRaises(registry.ToolError):
			methods.call_method("frappe.db.sql", {})

	def test_call_method_allows_listed_path(self):
		out = methods.call_method("erpnext.ai.tools.methods.echo", {"value": "hi"})
		self.assertEqual(out, "hi")

	def test_call_method_empty_allowlist_denies_everything(self):
		# Opposite polarity to enabled_tools: empty means deny-all here, not
		# "everything permitted".
		doc = frappe.get_single("AI Settings")
		doc.allowed_methods = ""
		doc.save()
		with self.assertRaises(registry.ToolError):
			methods.call_method("erpnext.ai.tools.methods.echo", {"value": "hi"})


class TestMCPWriteToolsPermissionBoundary(IntegrationTestCase):
	"""A low-privilege user must be refused through tools/call, not just in the
	ORM -- mirrors TestMCPPermissionBoundary in test_read_tools.py, for the
	write side.
	"""

	def setUp(self):
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		self.company = frappe.db.get_value("Company", {}, "name")
		doc.erpnext_company = self.company
		doc.save()

		self.user_email = "ai-write-lowpriv@example.com"
		if not frappe.db.exists("User", self.user_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": self.user_email,
					"first_name": "Low Priv Write",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_missing_and_unwritable_document_give_the_same_message(self):
		from erpnext.ai import mcp

		# Sales User (self.user_email's only role) has no permission at all on
		# Note, so this is a genuinely existing-but-unwritable record, not
		# just another "doesn't exist" case in disguise.
		note = frappe.get_doc({"doctype": "Note", "title": "write-boundary"})
		note.insert(ignore_permissions=True)

		frappe.set_user(self.user_email)
		missing = mcp.dispatch(
			{
				"jsonrpc": "2.0",
				"id": 1,
				"method": "tools/call",
				"params": {
					"name": "delete_document",
					"arguments": {"doctype": "Note", "name": "NOTE-DOES-NOT-EXIST"},
				},
			}
		)
		unwritable = mcp.dispatch(
			{
				"jsonrpc": "2.0",
				"id": 2,
				"method": "tools/call",
				"params": {"name": "delete_document", "arguments": {"doctype": "Note", "name": note.name}},
			}
		)

		missing_text = missing["result"]["content"][0]["text"]
		unwritable_text = unwritable["result"]["content"][0]["text"]
		self.assertTrue(missing["result"]["isError"])
		self.assertTrue(unwritable["result"]["isError"])
		self.assertEqual(missing_text, unwritable_text)
		self.assertEqual(missing_text, frappe._("Not permitted for the current ERPNext user."))

		# Sanity check: the document must still exist -- proves the
		# "unwritable" branch was a genuine denial, not a delete that
		# silently no-op'd.
		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("Note", note.name))
