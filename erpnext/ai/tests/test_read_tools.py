# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from unittest import mock

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry, scoping
from erpnext.ai.tools import documents, reports


class TestReadTools(IntegrationTestCase):
	def setUp(self):
		self.company = frappe.db.get_value("Company", {}, "name")
		doc = frappe.get_single("AI Settings")
		doc.erpnext_company = self.company
		doc.additional_companies = []
		doc.max_batch_size = 5
		doc.save()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _make_second_company(self) -> str:
		"""Return a second Company, creating a throwaway one if the site only has one.

		Mirrors TestScoping._make_second_company (test_scoping.py):
		ignore_chart_of_accounts skips Company.on_update()'s default-accounts and
		default-warehouses creation (the latter needs a "Warehouse Type: Transit"
		fixture this trimmed test site doesn't have) -- irrelevant to what these
		tests check, and the whole insert is rolled back in tearDown.
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
					"company_name": "AI Read Tools Test Co",
					"default_currency": "INR",
					"country": "India",
				}
			)
			doc.insert(ignore_permissions=True)
			return doc.name
		finally:
			frappe.local.flags.ignore_chart_of_accounts = previous_flag

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

	def test_search_documents_denies_user_without_permission_same_as_unknown_doctype(self):
		# IMPORTANT 5: GL Entry read is restricted to Accounts User/Manager/
		# Auditor -- mirrors describe_doctype's identical pattern
		# (test_discovery.py). Before this fix, frappe.get_list()'s own
		# permission check let this surface as a bare frappe.PermissionError,
		# distinguishable through the MCP transport from the ToolError an
		# unknown doctype name raises -- an existence oracle for custom
		# doctypes.
		user_email = "ai-search-lowpriv@example.com"
		if not frappe.db.exists("User", user_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user_email,
					"first_name": "AI Search Low Priv",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

		frappe.set_user(user_email)
		try:
			with self.assertRaises(registry.ToolError) as ctx:
				documents.search_documents("GL Entry")
		finally:
			frappe.set_user("Administrator")

		self.assertEqual(str(ctx.exception), frappe._("No such doctype: {0}").format("GL Entry"))

	def test_search_documents_rejects_deleted_document_doctype(self):
		# Deleted Document stores the full JSON of any deleted document, any
		# company included, with no `company` field to scope on.
		with self.assertRaises(registry.ToolError):
			documents.search_documents("Deleted Document", fields=["name"])

	def test_get_document_includes_child_tables(self):
		contact = frappe.get_doc({"doctype": "Contact", "first_name": "AI Read Tools Test Contact"})
		contact.append("phone_nos", {"phone": "+1-555-0100"})
		contact.insert(ignore_permissions=True)

		out = documents.get_document("Contact", contact.name)

		self.assertEqual(out["name"], contact.name)
		self.assertEqual(out["doctype"], "Contact")
		self.assertEqual(len(out["phone_nos"]), 1)
		self.assertEqual(out["phone_nos"][0]["phone"], "+1-555-0100")

	def test_get_missing_document_raises_does_not_exist_error(self):
		# No frappe.db.exists() pre-check anymore (see documents.get_document):
		# that was permission-free and re-opened a record-enumeration oracle, so
		# a missing name now surfaces as frappe.DoesNotExistError straight from
		# get_doc() itself. It's the MCP transport, not this direct call, that
		# normalises it into the same message as an existing-but-unreadable
		# record -- see
		# TestMCPPermissionBoundary.test_get_document_same_message_for_missing_and_unreadable_record.
		with self.assertRaises(frappe.DoesNotExistError):
			documents.get_document("Company", "No Such Company Ltd")

	def test_get_document_strips_permlevel_field_without_permlevel_access(self):
		# Timesheet Detail.billing_rate/costing_rate are permlevel 1, read-gated
		# to roles like Accounts User -- Projects User (granted below) is not
		# one of them, so as_dict() must not hand these over unfiltered.
		ts = frappe.get_doc(
			{
				"doctype": "Timesheet",
				"company": self.company,
				"time_logs": [{"is_billable": 1, "billing_rate": 999, "costing_rate": 888}],
			}
		)
		ts.insert(ignore_permissions=True)

		user_email = "ai-permlevel-test@example.com"
		if not frappe.db.exists("User", user_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user_email,
					"first_name": "AI Permlevel Test",
					"send_welcome_email": 0,
					"roles": [{"role": "Projects User"}],
				}
			).insert(ignore_permissions=True)

		frappe.set_user(user_email)
		out = documents.get_document("Timesheet", ts.name)
		frappe.set_user("Administrator")

		# apply_fieldlevel_read_permissions() deletes the instance attribute,
		# but get_valid_dict() iterates the doctype's full field list and
		# type-coerces the now-missing value -- so the key survives in the
		# dict, reset to the Currency fieldtype's falsy default, not the real
		# 999/888. That's the same shape frappe.client.get() returns for any
		# permlevel field on a normal document read, so check the value was
		# neutralised rather than that the key vanished.
		row = out["time_logs"][0]
		self.assertFalse(row.get("billing_rate"))
		self.assertFalse(row.get("costing_rate"))

		# Sanity check: Administrator (who apply_fieldlevel_read_permissions()
		# never strips fields for) still sees the real values, so this isn't
		# passing because the field was never actually saved.
		admin_out = documents.get_document("Timesheet", ts.name)
		self.assertEqual(admin_out["time_logs"][0]["billing_rate"], 999)

	def test_get_document_rejects_version_doctype(self):
		with self.assertRaises(registry.ToolError):
			documents.get_document("Version", "irrelevant")

	def test_get_document_rejects_deleted_document_doctype(self):
		with self.assertRaises(registry.ToolError):
			documents.get_document("Deleted Document", "irrelevant")

	def test_run_report_rejects_unknown_report(self):
		with self.assertRaises(registry.ToolError):
			reports.run_report("Not A Report")

	def test_run_report_denies_user_without_permission_same_as_unknown_report(self):
		# IMPORTANT 5: General Ledger's ref_doctype is GL Entry, read-restricted
		# to Accounts User/Manager/Auditor -- Sales User has none of those.
		# Before this fix, get_report_doc() inside run_query_report() let that
		# surface as a bare frappe.PermissionError, distinguishable from the
		# ToolError a genuinely unknown report name raises -- reintroducing
		# exactly what list_reports' permission filter exists to hide.
		user_email = "ai-report-lowpriv@example.com"
		if not frappe.db.exists("User", user_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user_email,
					"first_name": "AI Report Low Priv",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

		frappe.set_user(user_email)
		try:
			with self.assertRaises(registry.ToolError) as ctx:
				reports.run_report("General Ledger")
		finally:
			frappe.set_user("Administrator")

		self.assertEqual(str(ctx.exception), frappe._("No such report: {0}").format("General Ledger"))

	def test_run_report_executes_successfully(self):
		out = reports.run_report(
			"General Ledger", filters={"from_date": "2020-01-01", "to_date": "2030-01-01"}
		)
		self.assertIn("columns", out)
		self.assertIsInstance(out["rows"], list)
		self.assertEqual(out["filters"]["company"], self.company)
		self.assertFalse(out["truncated"])
		self.assertEqual(out["total_rows"], len(out["rows"]))

	def test_run_report_truncates_rows_over_cap_and_reports_total(self):
		# max_batch_size=5 from setUp; fake more rows than that come back from
		# the report engine itself so this isolates run_report()'s own
		# truncation logic from needing real GL Entry fixtures to exceed it.
		fake_result = {"columns": ["name"], "result": [{"name": f"Row {i}"} for i in range(10)]}
		with mock.patch("erpnext.ai.tools.reports.run_query_report", return_value=fake_result):
			out = reports.run_report(
				"General Ledger", filters={"from_date": "2020-01-01", "to_date": "2030-01-01"}
			)
		self.assertEqual(len(out["rows"]), 5)
		self.assertTrue(out["truncated"])
		self.assertEqual(out["total_rows"], 10)

	def test_run_report_rejects_non_dict_filters(self):
		# Frappe's list filter form; dict(filters or {}) would otherwise raise a
		# bare ValueError here, escaping this security-adjacent function uncaught.
		with self.assertRaises(registry.ToolError):
			reports.run_report("General Ledger", filters=[["company", "=", self.company]])

	def test_run_report_rejects_out_of_scope_company(self):
		with self.assertRaises(registry.ToolError):
			reports.run_report("General Ledger", filters={"company": "Nope Ltd"})

	def test_multi_company_site_scopes_company_and_refuses_unscopable_paths(self):
		other_company = self._make_second_company()
		# AI Settings (see setUp) still binds only self.company -- other_company
		# exists on the site but is out of this agent's bound_companies().

		names = [row["name"] for row in documents.search_documents("Company", fields=["name"])]
		self.assertIn(self.company, names)
		self.assertNotIn(other_company, names)

		# ToolError, not PermissionError: get_document's post-read company
		# check raises frappe.PermissionError (see
		# TestMCPPermissionBoundary.test_get_document_out_of_scope_company_same_message_as_missing_record),
		# but that only matters through the MCP transport, which is what
		# collapses it into the uniform "Not permitted" message. Direct calls
		# see the real exception type.
		with self.assertRaises(frappe.PermissionError):
			documents.get_document("Company", other_company)

		# Company-less doctypes stop being usable the instant a second Company
		# exists -- nothing here can guarantee it won't mix the two entities.
		with self.assertRaises(registry.ToolError):
			scoping.assert_scopable("Currency")

		with self.assertRaises(registry.ToolError):
			reports.run_report("General Ledger")


class TestMCPPermissionBoundary(IntegrationTestCase):
	"""A low-privilege user must be refused through tools/call, not just in the ORM."""

	def setUp(self):
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		self.company = frappe.db.get_value("Company", {}, "name")
		doc.erpnext_company = self.company
		# IMPORTANT 4's transport-level role gate (mcp._route -> paperclip.
		# assert_ai_user) would otherwise refuse this class's low-priv user
		# before tools/call ever runs -- this class exists to test the
		# *document*-level permission boundary tools/call enforces, not the
		# workspace-access gate, so the low-priv role must be explicitly
		# admitted here.
		doc.allowed_roles = '["System Manager", "Sales User"]'
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

	def _make_second_company(self) -> str:
		"""Return a second Company, creating a throwaway one if the site only has one.

		Mirrors TestReadTools._make_second_company (this module).
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
					"company_name": "AI MCP Boundary Test Co",
					"default_currency": "INR",
					"country": "India",
				}
			)
			doc.insert(ignore_permissions=True)
			return doc.name
		finally:
			frappe.local.flags.ignore_chart_of_accounts = previous_flag

	def _readmit_low_priv_user_after_rollback(self):
		"""Re-apply setUp()'s entire AI Settings save after a dispatch() call
		that rolled back the transaction.

		CRITICAL 2: every refusal branch in tools/call now calls
		frappe.db.rollback() so a failed call can't leave partial writes
		behind -- correct, but it also undoes anything else uncommitted in
		this test's transaction, including *all* of setUp()'s AI Settings
		save, not just allowed_roles (re-setting allowed_roles alone on a
		freshly-reloaded doc still leaves `enabled` reverted to its
		pre-setUp value, which then fails the `enabled` check right after the
		role gate). Tests below that make two dispatch() calls must fully
		re-admit this class's low-priv user before the second call.
		"""
		frappe.set_user("Administrator")
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		doc.erpnext_company = self.company
		doc.allowed_roles = '["System Manager", "Sales User"]'
		doc.save()
		frappe.set_user(self.user_email)

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
		# IMPORTANT 5: search_documents now collapses "exists but unreadable"
		# into the same "No such doctype" wording an unknown doctype name
		# gets (see TestReadTools.
		# test_search_documents_denies_user_without_permission_same_as_unknown_doctype)
		# -- still a refusal that reveals nothing about records, just no
		# longer distinguishable from "doctype doesn't exist".
		self.assertEqual(text, frappe._("No such doctype: {0}").format("GL Entry"))
		self.assertNotIn("rows", text.lower())

	def test_get_document_same_message_for_missing_and_unreadable_record(self):
		from erpnext.ai import mcp

		# Sales User (self.user_email's only role) has no read permission at
		# all on Timesheet, so this is a genuinely existing-but-unreadable
		# record, not just another "doesn't exist" case in disguise.
		company = frappe.db.get_value("Company", {}, "name")
		ts = frappe.get_doc({"doctype": "Timesheet", "company": company, "time_logs": [{"is_billable": 0}]})
		ts.insert(ignore_permissions=True)

		frappe.set_user(self.user_email)
		# CRITICAL 2: the unreadable-record call below also lands in the
		# rollback-triggering PermissionError branch, so it must run *before*
		# anything else rolls back the transaction `ts` was inserted in --
		# otherwise `ts` itself would be gone by the time this call looks it up.
		unreadable = mcp.dispatch(
			{
				"jsonrpc": "2.0",
				"id": 1,
				"method": "tools/call",
				"params": {"name": "get_document", "arguments": {"doctype": "Timesheet", "name": ts.name}},
			}
		)

		self._readmit_low_priv_user_after_rollback()
		missing = mcp.dispatch(
			{
				"jsonrpc": "2.0",
				"id": 2,
				"method": "tools/call",
				"params": {
					"name": "get_document",
					"arguments": {"doctype": "Timesheet", "name": "TS-DOES-NOT-EXIST"},
				},
			}
		)

		missing_text = missing["result"]["content"][0]["text"]
		unreadable_text = unreadable["result"]["content"][0]["text"]
		self.assertTrue(missing["result"]["isError"])
		self.assertTrue(unreadable["result"]["isError"])
		self.assertEqual(missing_text, unreadable_text)
		self.assertEqual(missing_text, frappe._("Not permitted for the current ERPNext user."))

	def test_get_document_same_message_for_missing_and_out_of_scope_company(self):
		from erpnext.ai import mcp

		# Sales User (self.user_email's only role) has read=1 on Company, so
		# this is a genuinely existing-and-ACL-readable record in a company
		# AI Settings does not bind -- the exact case documents.get_document's
		# company-scope check exists to guard, not just another "doesn't
		# exist" case in disguise. Before this fix, that check raised its own
		# ToolError (naming the company and what's permitted), which mcp.py's
		# tools/call handler echoed verbatim -- distinguishable from both the
		# missing-record and unreadable-record messages, and so itself an
		# existence oracle for out-of-scope companies.
		other_company = self._make_second_company()

		frappe.set_user(self.user_email)
		# CRITICAL 2: same ordering requirement as the unreadable-record test
		# above -- this call's own PermissionError branch rolls back the
		# transaction other_company was created in, so it must run first.
		out_of_scope = mcp.dispatch(
			{
				"jsonrpc": "2.0",
				"id": 1,
				"method": "tools/call",
				"params": {
					"name": "get_document",
					"arguments": {"doctype": "Company", "name": other_company},
				},
			}
		)

		self._readmit_low_priv_user_after_rollback()
		missing = mcp.dispatch(
			{
				"jsonrpc": "2.0",
				"id": 2,
				"method": "tools/call",
				"params": {
					"name": "get_document",
					"arguments": {"doctype": "Company", "name": "No Such Company Ltd"},
				},
			}
		)

		missing_text = missing["result"]["content"][0]["text"]
		out_of_scope_text = out_of_scope["result"]["content"][0]["text"]
		self.assertTrue(missing["result"]["isError"])
		self.assertTrue(out_of_scope["result"]["isError"])
		self.assertEqual(missing_text, out_of_scope_text)
		self.assertEqual(missing_text, frappe._("Not permitted for the current ERPNext user."))
