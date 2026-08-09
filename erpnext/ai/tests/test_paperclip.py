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

	def test_non_2xx_error_message_does_not_echo_the_remote_response_body(self):
		# The remote's response body is untrusted content -- it must never
		# ride along verbatim in an exception message that could reach an
		# HTTP response. Only the status code may appear; the body goes to
		# the Error Log instead.
		secret_looking_body = "internal-detail token=abc123 stack trace at line 42"
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 502
			req.return_value.text = secret_looking_body
			with self.assertRaises(paperclip.PaperclipError) as ctx:
				paperclip.get_client().health()
		self.assertNotIn(secret_looking_body, str(ctx.exception))
		self.assertIn("502", str(ctx.exception))

	def test_whitelisted_proxy_converts_paperclip_error_to_a_fixed_message(self):
		# _sanitize_paperclip_errors is the belt to PaperclipClient.request()'s
		# braces: even if a PaperclipError message ever carried remote detail
		# again, every whitelisted proxy must still only ever surface one
		# fixed, non-echoing message to the caller.
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 500
			req.return_value.text = "leaked internal secret xyz"
			with self.assertRaises(frappe.ValidationError) as ctx:
				paperclip.list_approvals()
		self.assertNotIn("leaked internal secret xyz", str(ctx.exception))

	def test_get_thread_is_post_only(self):
		# get_thread's _standing_issue() call can create a Paperclip issue as
		# a side effect -- a state-changing action must not be reachable via
		# a CSRF-exempt GET.
		self.assertEqual(
			frappe.allowed_http_methods_for_whitelisted_func.get(paperclip.get_thread), ("POST",)
		)

	def test_get_run_events_normalises_a_bare_list_from_paperclip(self):
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 200
			req.return_value.json.return_value = [{"seq": 1, "eventType": "status"}]
			out = paperclip.get_run_events("run-1")
		self.assertEqual(out, {"events": [{"seq": 1, "eventType": "status"}]})

	def test_get_run_events_normalises_an_already_wrapped_dict_from_paperclip(self):
		# If Paperclip's own reply is already {"events": [...]}, naively
		# wrapping it again as {"events": <that dict>} would make
		# fresh?.length undefined forever on the frontend -- the feed would
		# poll without ever showing anything.
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 200
			req.return_value.json.return_value = {"events": [{"seq": 1, "eventType": "status"}]}
			out = paperclip.get_run_events("run-1")
		self.assertEqual(out, {"events": [{"seq": 1, "eventType": "status"}]})

	def test_get_run_events_normalises_an_unexpected_shape_to_an_empty_list(self):
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 200
			req.return_value.json.return_value = {"unexpected": "shape"}
			out = paperclip.get_run_events("run-1")
		self.assertEqual(out, {"events": []})

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
