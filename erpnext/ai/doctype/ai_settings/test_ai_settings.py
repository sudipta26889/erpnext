# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

# erpnext_company (and AI Company Item.company) link to Company, whose own auto-generated test
# records transitively import erpnext/tests/utils.py's module-level BootStrapTestData(), which on
# this site collides with an existing real Fiscal Year and crashes at import time. Both tests here
# only need a Company that already exists on the site (frappe.db.get_value lookup), never a
# synthetic one, so skip the dependency entirely — same rationale as taskpilot_settings' use of
# IGNORE_TEST_RECORD_DEPENDENCIES for Cost Center.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Company"]


class TestAISettings(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_enabling_without_credentials_is_rejected(self):
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = ""
		doc.paperclip_company_id = ""
		doc.agent_id = ""
		doc.erpnext_company = ""
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_enabling_requires_a_bound_company(self):
		company = frappe.db.get_value("Company", {}, "name")
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com/"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		doc.erpnext_company = ""
		self.assertRaises(frappe.ValidationError, doc.save)

		# Document.save() stamps self.modified with now() during set_user_and_timestamp() before
		# validate() runs, and never undoes that when validate() throws. Singles are never is_new(),
		# so the next save()'s check_if_latest() compares that stale in-memory stamp against the
		# still-unpersisted (None) DB value and raises TimestampMismatchError. Nothing was actually
		# written above, so resetting modified back to None is correct, not a hack around real state.
		doc.modified = None
		doc.erpnext_company = company
		doc.save()
		# trailing slash is normalised away so URL joins never double up
		self.assertEqual(doc.paperclip_url, "https://paperclip.example.com")

	def test_defaults_are_conservative(self):
		doc = frappe.get_single("AI Settings")
		self.assertEqual(doc.max_batch_size, 20)
		self.assertEqual(doc.max_document_value, 0)
