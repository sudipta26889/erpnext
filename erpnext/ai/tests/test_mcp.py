# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request

from erpnext.ai import mcp, registry

# Throwaway tools registered purely to exercise dispatch()'s exception contract.
# Registered at import time (matching the normal `registry.tool` usage pattern)
# and torn down in TestMCP.tearDownClass so they don't leak into other tests'
# expectations of the tool catalogue (e.g. exact-list assertions elsewhere).
_THROWAWAY_TOOL_NAMES = [
	"_test_raises_does_not_exist",
	"_test_raises_validation",
	"_test_raises_unexpected",
]


@registry.tool(
	name="_test_raises_does_not_exist",
	description="Test-only tool that raises DoesNotExistError.",
	input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def _raise_does_not_exist():
	# Message deliberately names a specific record, mirroring what a real
	# frappe.get_doc() lookup failure looks like — this must never reach the caller.
	raise frappe.DoesNotExistError("Sales Invoice ABC-001 not found")


@registry.tool(
	name="_test_raises_validation",
	description="Test-only tool that raises ValidationError.",
	input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def _raise_validation():
	raise frappe.ValidationError("Customer is required")


@registry.tool(
	name="_test_raises_unexpected",
	description="Test-only tool that raises an unclassified error.",
	input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def _raise_unexpected():
	raise RuntimeError("leaked detail: tabSales Invoice constraint violated")


class TestMCP(IntegrationTestCase):
	@classmethod
	def tearDownClass(cls):
		for name in _THROWAWAY_TOOL_NAMES:
			registry._REGISTRY.pop(name, None)
		super().tearDownClass()

	def setUp(self):
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
		doc.enabled_tools = ""
		doc.save()

	def tearDown(self):
		frappe.db.rollback()

	def _handle(self, body: str):
		"""Simulate a POST to the MCP endpoint and call handle() for real.

		Uses frappe.utils.set_request (werkzeug EnvironBuilder under the hood),
		the same helper frappe's own test suite uses to fake a request — it
		produces a real werkzeug Request that frappe.request.get_data(as_text=True)
		reads correctly, which a hand-rolled stub would only approximate.
		"""
		original_request = getattr(frappe.local, "request", None)
		set_request(
			method="POST",
			path="/api/method/erpnext.ai.mcp.handle",
			data=body,
			content_type="application/json",
		)
		try:
			return mcp.handle()
		finally:
			frappe.local.request = original_request

	def test_initialize_returns_protocol_and_instructions(self):
		out = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
		self.assertEqual(out["id"], 1)
		self.assertEqual(out["result"]["protocolVersion"], mcp.PROTOCOL_VERSION)
		self.assertIn("tools", out["result"]["capabilities"])
		# Orientation must name the bound company so the agent is never guessing.
		self.assertIn("ERPNext", out["result"]["instructions"])

	def test_tools_list_exposes_schemas(self):
		out = mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
		names = [t["name"] for t in out["result"]["tools"]]
		self.assertIn("ping", names)
		entry = next(t for t in out["result"]["tools"] if t["name"] == "ping")
		self.assertEqual(entry["inputSchema"]["type"], "object")

	def test_tools_call_returns_text_content(self):
		out = mcp.dispatch(
			{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
			 "params": {"name": "ping", "arguments": {}}}
		)
		payload = json.loads(out["result"]["content"][0]["text"])
		self.assertTrue(payload["pong"])
		self.assertFalse(out["result"].get("isError", False))

	def test_tool_error_is_reported_as_content_not_transport_error(self):
		out = mcp.dispatch(
			{"jsonrpc": "2.0", "id": 4, "method": "tools/call",
			 "params": {"name": "nope", "arguments": {}}}
		)
		# MCP convention: tool failures are results with isError, so the model can
		# read and adapt rather than the transport blowing up.
		self.assertTrue(out["result"]["isError"])
		self.assertIn("nope", out["result"]["content"][0]["text"])

	def test_unknown_method_is_a_jsonrpc_error(self):
		out = mcp.dispatch({"jsonrpc": "2.0", "id": 5, "method": "bogus/thing", "params": {}})
		self.assertEqual(out["error"]["code"], -32601)

	def test_disabled_settings_refuses_everything(self):
		doc = frappe.get_single("AI Settings")
		doc.enabled = 0
		doc.save()
		out = mcp.dispatch({"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}})
		self.assertEqual(out["error"]["code"], -32001)

	def test_notification_returns_no_response(self):
		# JSON-RPC notifications have no id and must not be answered.
		self.assertIsNone(mcp.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))

	def test_dispatch_guards_non_dict_payload_without_going_through_handle(self):
		# dispatch() is a published interface in its own right, not reached
		# only via handle() — the guard must live here too, or the next direct
		# caller reopens the AttributeError-on-payload.get() crash.
		for payload in (None, 42, "x", True, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]):
			with self.subTest(payload=payload):
				out = mcp.dispatch(payload)
				self.assertEqual(out["error"]["code"], mcp.INVALID_REQUEST)

	def test_malformed_enabled_tools_never_escapes_any_protocol_branch_as_a_500(self):
		# registry.get_tools() raises ToolError when enabled_tools is malformed
		# JSON. frappe.db.set_single_value bypasses AISettings.validate()'s
		# JSON check — the same path a patch or script would use to flip a
		# Single directly — so this is how the field legitimately ends up
		# malformed. Every protocol-level branch must still come back as a
		# well-formed JSON-RPC object, never an unhandled exception.
		frappe.db.set_single_value("AI Settings", "enabled_tools", "not valid json")

		for method, params in (
			("initialize", {}),
			("tools/list", {}),
			("tools/call", {"name": "ping", "arguments": {}}),
		):
			with self.subTest(method=method):
				out = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
				self.assertIsInstance(out, dict)
				self.assertTrue("result" in out or "error" in out)

	# -- dispatch()-level: exception-contract tests -----------------------

	def test_does_not_exist_error_returns_vague_message_not_record_name(self):
		# DoesNotExistError is a ValidationError subclass and must not fall
		# through to the verbatim-message branch: that would let a caller
		# distinguish "hidden" from "missing" records by their error text.
		out = mcp.dispatch(
			{"jsonrpc": "2.0", "id": 7, "method": "tools/call",
			 "params": {"name": "_test_raises_does_not_exist", "arguments": {}}}
		)
		text = out["result"]["content"][0]["text"]
		self.assertTrue(out["result"]["isError"])
		self.assertNotIn("Sales Invoice ABC-001", text)
		self.assertEqual(text, frappe._("Not permitted for the current ERPNext user."))

	def test_validation_error_is_returned_verbatim(self):
		out = mcp.dispatch(
			{"jsonrpc": "2.0", "id": 8, "method": "tools/call",
			 "params": {"name": "_test_raises_validation", "arguments": {}}}
		)
		text = out["result"]["content"][0]["text"]
		self.assertTrue(out["result"]["isError"])
		self.assertEqual(text, "Customer is required")

	def test_unexpected_error_returns_generic_message_not_original_text(self):
		out = mcp.dispatch(
			{"jsonrpc": "2.0", "id": 9, "method": "tools/call",
			 "params": {"name": "_test_raises_unexpected", "arguments": {}}}
		)
		text = out["result"]["content"][0]["text"]
		self.assertTrue(out["result"]["isError"])
		self.assertNotIn("tabSales Invoice", text)
		self.assertNotIn("leaked", text)
		self.assertEqual(
			text, frappe._("An internal error occurred in ERPNext; it has been logged.")
		)

	# -- handle()-level: the real HTTP-facing entrypoint -------------------

	def test_handle_empty_body_is_a_parse_error_no_exception_escapes(self):
		out = self._handle("")
		self.assertEqual(out["error"]["code"], mcp.PARSE_ERROR)

	def test_handle_invalid_json_is_a_parse_error_no_exception_escapes(self):
		out = self._handle("[oops")
		self.assertEqual(out["error"]["code"], mcp.PARSE_ERROR)

	def test_handle_non_object_json_is_invalid_request(self):
		# json.loads happily parses these; only dispatch()'s payload.get("id")
		# used to assume a dict, so each of these used to crash with AttributeError.
		for body in ("null", "42", '"x"', "true"):
			with self.subTest(body=body):
				out = self._handle(body)
				self.assertEqual(out["error"]["code"], mcp.INVALID_REQUEST)

	def test_handle_array_body_is_invalid_request_batching_removed(self):
		# MCP 2025-06-18 dropped JSON-RPC batching; an array body is no longer valid.
		body = json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}])
		out = self._handle(body)
		self.assertEqual(out["error"]["code"], mcp.INVALID_REQUEST)

	def test_handle_tools_call_ping_succeeds(self):
		body = json.dumps(
			{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
			 "params": {"name": "ping", "arguments": {}}}
		)
		out = self._handle(body)
		payload = json.loads(out["result"]["content"][0]["text"])
		self.assertTrue(payload["pong"])
		self.assertFalse(out["result"].get("isError", False))
