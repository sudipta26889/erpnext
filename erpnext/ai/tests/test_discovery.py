# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry, scoping
from erpnext.ai.tools import discovery


class TestDiscovery(IntegrationTestCase):
	def setUp(self):
		self.company = frappe.db.get_value("Company", {}, "name")
		doc = frappe.get_single("AI Settings")
		doc.erpnext_company = self.company
		doc.save()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _make_second_company(self) -> str:
		"""Return a second Company, creating a throwaway one if the site only has one.

		Mirrors TestReadTools._make_second_company (test_read_tools.py):
		ignore_chart_of_accounts skips Company.on_update()'s default-accounts and
		default-warehouses creation (the latter needs a "Warehouse Type: Transit"
		fixture this trimmed test site doesn't have) -- irrelevant to what this
		test checks, and the whole insert is rolled back in tearDown.
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
					"company_name": "AI Discovery Test Co",
					"default_currency": "INR",
					"country": "India",
				}
			)
			doc.insert(ignore_permissions=True)
			return doc.name
		finally:
			frappe.local.flags.ignore_chart_of_accounts = previous_flag

	def test_company_context_grounds_the_agent(self):
		out = discovery.get_company_context()
		self.assertEqual(out["company"], frappe.get_single("AI Settings").erpnext_company)
		self.assertTrue(out["currency"])
		self.assertIn("modules", out)

	def test_company_context_reports_counts_roots_and_accessible_companies(self):
		out = discovery.get_company_context()

		self.assertEqual(out["accessible_companies"], scoping.bound_companies())

		self.assertIsInstance(out["chart_of_accounts_roots"], list)
		for root in out["chart_of_accounts_roots"]:
			self.assertIn("name", root)
			self.assertIn("root_type", root)

		for doctype in ("Customer", "Supplier", "Item", "Sales Order", "Sales Invoice"):
			self.assertIn(doctype, out["counts"])
			self.assertIsInstance(out["counts"][doctype], int)
			self.assertGreaterEqual(out["counts"][doctype], 0)

		# A non-negative int is also what an unscoped frappe.db.count(doctype)
		# would return, so the assertions above would still pass even if the
		# company filter silently regressed away. Prove scoping directly: seed
		# a second Company outside AI Settings' bound scope (see setUp -- only
		# self.company is bound), create a Sales Order against it, and confirm
		# the reported count doesn't grow to include it. ignore_mandatory/
		# ignore_links/flags.ignore_validate skip the full sales-transaction
		# validation (customer, items, delivery date, ...) that this trimmed
		# test site has no Customer/Item fixtures to satisfy -- irrelevant to
		# what this test checks, which only needs a row that exists and
		# carries the out-of-scope company.
		other_company = self._make_second_company()
		before = discovery.get_company_context()["counts"]["Sales Order"]

		so = frappe.get_doc({"doctype": "Sales Order", "company": other_company})
		so.flags.ignore_validate = True
		so.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True)

		after = discovery.get_company_context()["counts"]["Sales Order"]
		self.assertEqual(after, before)

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

	def test_describe_doctype_denies_user_without_read_permission_same_as_nonexistent(self):
		# GL Entry read is restricted to Accounts User/Manager/Auditor -- Sales
		# User has none of those, so this must be refused, not schema-dumped.
		user_email = "ai-discovery-lowpriv@example.com"
		if not frappe.db.exists("User", user_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user_email,
					"first_name": "AI Discovery Low Priv",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

		frappe.set_user(user_email)
		try:
			with self.assertRaises(registry.ToolError) as ctx:
				discovery.describe_doctype("GL Entry")
		finally:
			frappe.set_user("Administrator")

		# Same wording template as the nonexistent-doctype branch (see
		# test_describe_unknown_doctype_raises_tool_error) applied to the same
		# name the caller asked about -- not usable to tell "GL Entry exists but
		# you can't read it" apart from "GL Entry doesn't exist at all".
		self.assertEqual(str(ctx.exception), frappe._("No such doctype: {0}").format("GL Entry"))

	def test_list_reports_returns_entries(self):
		out = discovery.list_reports("Accounts")
		self.assertTrue(out)
		self.assertIn("name", out[0])

	def test_workspace_map_locates_doctypes(self):
		out = discovery.get_workspace_map()
		self.assertIn("Sales Invoice", out)
		self.assertIn("workspace", out["Sales Invoice"])
