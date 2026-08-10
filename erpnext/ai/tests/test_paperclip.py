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

	def test_connection_failure_never_escapes_as_a_raw_requests_error(self):
		# A refused connection or read timeout must land on the same sanitised
		# path as a non-2xx: raw RequestExceptions carry the internal URL and
		# would reach the HTTP response through the traceback.
		import requests as requests_module

		with patch(
			"erpnext.ai.paperclip.requests.request",
			side_effect=requests_module.ConnectionError("http://internal-host:3100 refused"),
		):
			with self.assertRaises(paperclip.PaperclipError) as ctx:
				paperclip.get_client().health()
		self.assertNotIn("internal-host", str(ctx.exception))

	def _sse(self, *frames: str):
		"""A mocked streaming response yielding raw SSE lines."""
		req = patch("erpnext.ai.paperclip.requests.request").start()
		self.addCleanup(patch.stopall)
		req.return_value.status_code = 200
		req.return_value.iter_lines.return_value = iter(frames)
		return req

	def test_board_chat_joins_the_stream_into_one_answer(self):
		req = self._sse(
			'data: {"type":"start","issueId":"i-9"}',
			"",
			'data: {"type":"chunk","text":"BOARD "}',
			'data: {"type":"chunk","text":"CHAT OK"}',
			'data: {"type":"done","issueId":"i-9","exitCode":0,"timedOut":false}',
		)
		out = paperclip.get_client().chat("hi")
		self.assertEqual(out, {"issue_id": "i-9", "answer": "BOARD CHAT OK", "timed_out": False})
		self.assertEqual(req.call_args.args[1], "https://paperclip.example.com/api/board/chat/stream")
		self.assertEqual(req.call_args.kwargs["json"], {"companyId": "c-1", "message": "hi"})
		self.assertTrue(req.call_args.kwargs["stream"])

	def test_board_chat_reports_a_server_side_timeout(self):
		self._sse(
			'data: {"type":"chunk","text":"partial"}',
			'data: {"type":"done","issueId":"i-9","exitCode":0,"timedOut":true}',
		)
		out = paperclip.get_client().chat("hi")
		self.assertTrue(out["timed_out"])
		self.assertEqual(out["answer"], "partial")

	def test_board_chat_error_frame_does_not_echo_the_remote_message(self):
		self._sse('data: {"type":"error","message":"leaked internal secret xyz"}')
		with self.assertRaises(paperclip.PaperclipError) as ctx:
			paperclip.get_client().chat("hi")
		self.assertNotIn("leaked internal secret xyz", str(ctx.exception))

	def test_board_chat_ignores_frames_it_cannot_parse(self):
		# Heartbeat/comment lines and half-written frames must not take the
		# whole answer down.
		self._sse(
			": keepalive",
			"data: not-json",
			'data: {"type":"chunk","text":"ok"}',
			'data: {"type":"done","issueId":"i-9"}',
		)
		self.assertEqual(paperclip.get_client().chat("hi")["answer"], "ok")

	def test_board_chat_anchors_on_a_task_when_given_one(self):
		req = self._sse('data: {"type":"done","issueId":"t-1"}')
		paperclip.get_client().chat("hi", task_id="t-1")
		self.assertEqual(req.call_args.kwargs["json"]["taskId"], "t-1")

	def test_board_chat_is_post_only(self):
		# It spawns an unrestricted agent process on the Paperclip host; that
		# must not be reachable through a CSRF-exempt GET.
		self.assertEqual(
			frappe.allowed_http_methods_for_whitelisted_func.get(paperclip.board_chat), ("POST",)
		)

	def test_get_board_thread_is_empty_before_the_first_chat(self):
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 200
			req.return_value.json.return_value = []
			out = paperclip.get_board_thread()
		self.assertEqual(out, {"issue_id": None, "comments": []})

	def test_get_board_thread_matches_the_title_exactly(self):
		# Paperclip's `q` is fuzzy: it returns near-misses too, and picking the
		# first row would anchor the desk tab on the wrong conversation.
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 200
			req.return_value.json.side_effect = [
				[
					{"id": "wrong", "title": "Board Operations — weekly CEO digest", "status": "todo"},
					{"id": "right", "title": "Board Operations", "status": "todo"},
				],
				[{"id": "c-1", "body": "hi", "authorUserId": "board-concierge"}],
			]
			out = paperclip.get_board_thread()
		self.assertEqual(out["issue_id"], "right")
		self.assertEqual(out["comments"][0]["id"], "c-1")

	def test_thread_comments_are_capped(self):
		# The Board Operations issue is shared with Paperclip's own UI and grows
		# without bound, while the tab re-polls it every few seconds.
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 200
			req.return_value.json.side_effect = [
				[{"id": "right", "title": "Board Operations", "status": "todo"}],
				[{"id": f"c-{i}", "body": "x"} for i in range(250)],
			]
			out = paperclip.get_board_thread()
		self.assertEqual(len(out["comments"]), paperclip.MAX_THREAD_COMMENTS)
		self.assertEqual(out["comments"][-1]["id"], "c-249")

	def test_list_approvals_flattens_paperclips_envelope(self):
		# Paperclip answers {"actionRequests": [{"request": {...}, "toolName": ...}]}.
		# Passing that through verbatim put an object where the SPA does .map(),
		# which throws during render and unmounts the whole tab -- a blank pane
		# with the reason only in the browser console.
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 200
			req.return_value.json.return_value = {
				"actionRequests": [
					{
						"request": {
							"id": "ar-1",
							"status": "pending",
							"createdAt": "2026-08-10T00:00:00Z",
							"canonicalArgumentsSummary": {"summary": '{"doctype": "Item"}'},
						},
						"toolName": "mcp.erpnext:erpnext-doc-update",
						"riskLevel": "write",
						"applicationName": "ERPNext",
					}
				]
			}
			out = paperclip.list_approvals()
		self.assertIsInstance(out["action_requests"], list)
		row = out["action_requests"][0]
		self.assertEqual(row["id"], "ar-1")
		self.assertEqual(row["risk"], "write")
		self.assertEqual(row["toolName"], "mcp.erpnext:erpnext-doc-update")
		self.assertIn("Item", row["summary"])

	def test_list_approvals_survives_an_unexpected_shape(self):
		for payload in ({"unexpected": "shape"}, [], {"actionRequests": None}):
			with self.subTest(payload=payload):
				with patch("erpnext.ai.paperclip.requests.request") as req:
					req.return_value.status_code = 200
					req.return_value.json.return_value = payload
					self.assertEqual(paperclip.list_approvals(), {"action_requests": []})

	def test_role_gate_blocks_users_without_an_allowed_role(self):
		with patch("erpnext.ai.paperclip.frappe.get_roles", return_value=["Stock User"]):
			self.assertRaises(frappe.PermissionError, paperclip.assert_ai_user)

	def test_role_gate_allows_listed_role(self):
		with patch("erpnext.ai.paperclip.frappe.get_roles", return_value=["System Manager"]):
			paperclip.assert_ai_user()
