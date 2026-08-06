# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import mcp, registry


class TestMCP(IntegrationTestCase):
	def setUp(self):
		registry.settings.cache_clear()
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
		doc.enabled_tools = ""
		doc.save()
		registry.settings.cache_clear()

	def tearDown(self):
		registry.settings.cache_clear()
		frappe.db.rollback()

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
		registry.settings.cache_clear()
		out = mcp.dispatch({"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}})
		self.assertEqual(out["error"]["code"], -32001)

	def test_notification_returns_no_response(self):
		# JSON-RPC notifications have no id and must not be answered.
		self.assertIsNone(mcp.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))
