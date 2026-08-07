# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import paperclip, registry


class TestPaperclip(IntegrationTestCase):
	def setUp(self):
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
		doc.allowed_roles = '["System Manager"]'
		doc.save()

	def tearDown(self):
		frappe.db.rollback()

	def test_client_sends_bearer_token(self):
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 200
			req.return_value.json.return_value = {"status": "ok"}
			paperclip.get_client().health()
		headers = req.call_args.kwargs["headers"]
		self.assertEqual(headers["Authorization"], "Bearer secret")

	def test_non_2xx_raises(self):
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 403
			req.return_value.text = "nope"
			with self.assertRaises(paperclip.PaperclipError):
				paperclip.get_client().health()

	def test_url_is_joined_without_double_slash(self):
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 200
			req.return_value.json.return_value = {}
			paperclip.get_client().request("GET", "/api/health")
		self.assertEqual(req.call_args.args[1], "https://paperclip.example.com/api/health")

	def test_role_gate_blocks_users_without_an_allowed_role(self):
		with patch("erpnext.ai.paperclip.frappe.get_roles", return_value=["Stock User"]):
			self.assertRaises(frappe.PermissionError, paperclip.assert_ai_user)

	def test_role_gate_allows_listed_role(self):
		with patch("erpnext.ai.paperclip.frappe.get_roles", return_value=["System Manager"]):
			paperclip.assert_ai_user()
