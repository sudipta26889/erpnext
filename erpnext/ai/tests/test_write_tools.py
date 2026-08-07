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

	def test_create_document_rejects_docstatus_in_data(self):
		# CRITICAL: data={"docstatus": 1} on frappe.get_doc() is a real 0->1
		# submit transition (_action = "submit", on_submit() runs -- GL/stock
		# postings) even though it never went through submit_document, the
		# only tool name the external platform's approval policy gates.
		with self.assertRaises(registry.ToolError):
			documents.create_document(
				"Note", {"title": "sneaky", "docstatus": 1}, idempotency_key="k-docstatus-create"
			)
		# Nothing must have been created, submitted or otherwise -- rejection,
		# not a stripped-and-proceed fallback.
		self.assertFalse(frappe.db.exists("Note", {"title": "sneaky"}))

	def test_create_document_rejects_other_default_fields(self):
		# Not just docstatus -- the whole frappe.model.default_fields set plus
		# the child-table bookkeeping fields are caller-forbidden.
		for key, value in (
			("owner", "someone@example.com"),
			("name", "FORCED-NAME"),
			("creation", "2020-01-01"),
		):
			with self.subTest(key=key):
				with self.assertRaises(registry.ToolError):
					documents.create_document(
						"Note", {"title": "x", key: value}, idempotency_key=f"k-forbidden-{key}"
					)

	def test_create_document_rejects_write_forbidden_doctypes(self):
		# IMPORTANT 6: these have no `company` field, so they would otherwise
		# sail through assert_scopable() unchallenged on this single-company
		# site -- User/Role are privilege escalation, Server Script is code
		# execution around the call_method allowlist, AI Settings is the
		# agent widening its own caps.
		for doctype in ("User", "Role", "Server Script", "AI Settings"):
			with self.subTest(doctype=doctype):
				with self.assertRaises(registry.ToolError):
					documents.create_document(doctype, {}, idempotency_key=f"k-forbidden-doctype-{doctype}")

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

	def test_update_document_rejects_docstatus_in_data(self):
		# CRITICAL: update_document's docstatus != 0 guard only inspects the
		# *stored* value -- data={"docstatus": 1} on a draft is exactly the
		# 0->1 submit transition that must never reach doc.set()/doc.save().
		note = frappe.get_doc({"doctype": "Note", "title": "still-a-draft"}).insert()
		with self.assertRaises(registry.ToolError):
			documents.update_document("Note", note.name, {"docstatus": 1})
		note.reload()
		self.assertEqual(note.docstatus, 0)

	def test_update_document_rejects_write_forbidden_doctypes(self):
		# _load_for_write checks the doctype before ever loading the record,
		# so even a real, existing name (Administrator always exists) never
		# gets touched.
		with self.assertRaises(registry.ToolError):
			documents.update_document("User", "Administrator", {"roles": [{"role": "System Manager"}]})

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

	# -- idempotency ------------------------------------------------------

	def test_idempotency_key_reused_for_a_different_doctype_raises(self):
		# IMPORTANT 4: the key alone is not enough to identify what it was
		# for -- create_document("Sales Invoice", key="k") after
		# create_document("Quotation", key="k") must not silently hand back
		# the Quotation with reused: true.
		documents.create_document(
			"Note", {"title": "note-for-key-reuse"}, idempotency_key="cross-doctype-key"
		)
		with self.assertRaises(registry.ToolError):
			# Contact, not ToDo: ToDo is COMPANY_LESS_SENSITIVE and would
			# raise for a different reason before ever reaching the
			# doctype-mismatch check this test targets.
			documents.create_document("Contact", {}, idempotency_key="cross-doctype-key")

	def test_idempotency_key_of_a_deleted_document_is_not_permanently_poisoned(self):
		# IMPORTANT 5: _recall_idempotency returns None once the referenced
		# document is gone, so a new one gets created for the same key --
		# _remember_idempotency must then update the existing record in
		# place rather than raising DuplicateEntryError (which would abort
		# the transaction on PostgreSQL and roll back the very document just
		# created, permanently poisoning the key).
		first = documents.create_document(
			"Note", {"title": "will-be-deleted"}, idempotency_key="deleted-doc-key"
		)
		frappe.delete_doc("Note", first["name"], force=True, ignore_permissions=True)

		second = documents.create_document(
			"Note", {"title": "recreated-under-same-key"}, idempotency_key="deleted-doc-key"
		)
		self.assertFalse(second["reused"])
		self.assertNotEqual(second["name"], first["name"])
		self.assertTrue(frappe.db.exists("Note", second["name"]))

		record = frappe.db.get_value(
			"AI Idempotency Record", "deleted-doc-key", ["ref_doctype", "ref_name"], as_dict=True
		)
		self.assertEqual(record.ref_name, second["name"])

	def test_remember_idempotency_is_tolerant_of_a_concurrent_double_call(self):
		# Simulates two concurrent create_document calls that both raced past
		# _recall_idempotency before either had committed: both create their
		# own document, then both call _remember_idempotency for the same
		# key. Neither call may raise -- the loser must update the record in
		# place instead of hitting DuplicateEntryError, and the transaction
		# must remain usable afterwards (the same collision a PostgreSQL
		# aborted transaction would otherwise poison).
		doc_a = frappe.get_doc({"doctype": "Note", "title": "race-a"}).insert()
		doc_b = frappe.get_doc({"doctype": "Note", "title": "race-b"}).insert()

		documents._remember_idempotency("race-key", "Note", doc_a.name)
		documents._remember_idempotency("race-key", "Note", doc_b.name)  # must not raise

		record = frappe.db.get_value(
			"AI Idempotency Record", "race-key", ["ref_doctype", "ref_name"], as_dict=True
		)
		self.assertEqual(record.ref_name, doc_b.name)
		self.assertTrue(frappe.db.exists("Note", doc_b.name))

	# -- value cap ------------------------------------------------------

	def _two_plain_accounts(self, company: str) -> tuple[str, str]:
		"""Two non-group, party-free leaf accounts for `company`.

		Every Company gets a default chart of accounts on creation, so this
		needs no fixtures of its own -- just two ordinary blank-account-type
		leaf accounts that don't require a party (Receivable/Payable would).
		"""
		rows = frappe.get_all(
			"Account",
			filters={"company": company, "is_group": 0, "account_type": ""},
			fields=["name"],
			order_by="name",
			limit=2,
		)
		if len(rows) < 2:
			self.skipTest(f"{company} has no two plain leaf accounts to build a Journal Entry from.")
		return rows[0].name, rows[1].name

	def test_value_cap_checks_payment_entry_fields(self):
		# IMPORTANT 2b: Payment Entry settles through paid_amount/
		# base_paid_amount, not grand_total/base_grand_total/total -- the
		# original field list was a complete no-op for it.
		doc = frappe._dict(
			{"doctype": "Payment Entry", "name": "PE-0001", "paid_amount": 5000, "base_paid_amount": 5000}
		)
		settings = frappe.get_single("AI Settings")
		settings.max_document_value = 1000
		settings.save()
		with self.assertRaises(registry.ToolError):
			documents._assert_value_cap(doc)

	def test_value_cap_takes_the_maximum_of_present_fields(self):
		# Not "the first populated field" -- the highest one, so a doctype
		# that happens to populate an earlier-checked field with a small
		# value can't shadow a much larger one later in the list.
		doc = frappe._dict(
			{
				"doctype": "Journal Entry",
				"name": "JE-0001",
				"total": 1,
				"total_debit": 5000,
				"total_credit": 5000,
			}
		)
		settings = frappe.get_single("AI Settings")
		settings.max_document_value = 1000
		settings.save()
		with self.assertRaises(registry.ToolError):
			documents._assert_value_cap(doc)

	def test_create_document_enforces_value_cap_on_the_settled_journal_entry(self):
		# IMPORTANT 2a+2b together, through the real tool: pre-insert this
		# doc's total_debit/total_credit are unset (AccountsController.
		# validate() computes them *during* insert()), and grand_total/
		# base_grand_total/total (the old field list) don't exist on Journal
		# Entry at all -- both bugs combined made the cap a complete no-op.
		debit_account, credit_account = self._two_plain_accounts(self.company)
		settings = frappe.get_single("AI Settings")
		settings.max_document_value = 1000
		settings.save()

		before = frappe.db.count("Journal Entry")
		data = {
			"voucher_type": "Journal Entry",
			"posting_date": frappe.utils.today(),
			"company": self.company,
			"accounts": [
				{"account": debit_account, "debit_in_account_currency": 5000},
				{"account": credit_account, "credit_in_account_currency": 5000},
			],
		}
		with self.assertRaises(registry.ToolError):
			documents.create_document("Journal Entry", data, idempotency_key="je-over-cap")

		# The savepoint must leave nothing behind -- an over-cap document
		# must never be persisted, not even as an orphaned draft, and the key
		# must not be spent on the rejected attempt.
		self.assertEqual(frappe.db.count("Journal Entry"), before)
		self.assertIsNone(frappe.db.exists("AI Idempotency Record", "je-over-cap"))

	def test_create_document_journal_entry_within_cap_succeeds(self):
		# Regression guard: the savepoint/rollback plumbing must not get in
		# the way of a legitimate, within-cap creation.
		debit_account, credit_account = self._two_plain_accounts(self.company)
		data = {
			"voucher_type": "Journal Entry",
			"posting_date": frappe.utils.today(),
			"company": self.company,
			"accounts": [
				{"account": debit_account, "debit_in_account_currency": 100},
				{"account": credit_account, "credit_in_account_currency": 100},
			],
		}
		settings = frappe.get_single("AI Settings")
		settings.max_document_value = 1000
		settings.save()
		out = documents.create_document("Journal Entry", data, idempotency_key="je-within-cap")
		self.assertFalse(out["reused"])
		self.assertTrue(frappe.db.exists("Journal Entry", out["name"]))

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
