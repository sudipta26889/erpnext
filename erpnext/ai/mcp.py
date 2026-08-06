# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""MCP server for ERPNext, served as a single Frappe whitelisted endpoint.

MCP's streamable-HTTP transport is JSON-RPC over POST; SSE is only needed for
server-initiated messages, which a pure tool server never sends. Living inside
Frappe means `frappe.session.user` is already correct, so document permissions,
user permissions and field-level permissions are enforced by Frappe itself
instead of being reimplemented behind a sidecar.
"""

import json
from typing import Any

import frappe
from frappe import _

from erpnext.ai import registry
from erpnext.ai.registry import ToolError

PROTOCOL_VERSION = "2025-06-18"

METHOD_NOT_FOUND = -32601
INVALID_REQUEST = -32600
PARSE_ERROR = -32700
AI_DISABLED = -32001


def _error(request_id: Any, code: int, message: str) -> dict:
	return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, result: dict) -> dict:
	return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _text_content(payload: Any, is_error: bool = False) -> dict:
	text = payload if isinstance(payload, str) else frappe.as_json(payload, indent=None)
	return {"content": [{"type": "text", "text": text}], "isError": is_error}


def dispatch(payload: dict) -> dict | None:
	"""Handle one JSON-RPC request. Returns None for notifications."""
	request_id = payload.get("id")
	method = payload.get("method")

	if request_id is None:
		# Notification: acknowledge by doing nothing. Answering would violate JSON-RPC.
		return None

	if not method:
		return _error(request_id, INVALID_REQUEST, _("Missing method"))

	if not registry.settings().enabled:
		return _error(request_id, AI_DISABLED, _("ERPNext AI is disabled in AI Settings."))

	params = payload.get("params") or {}

	if method == "initialize":
		from erpnext.ai.knowledge import orientation_text

		return _result(
			request_id,
			{
				"protocolVersion": PROTOCOL_VERSION,
				"capabilities": {"tools": {"listChanged": False}},
				"serverInfo": {"name": "erpnext", "version": frappe.__version__},
				"instructions": orientation_text(),
			},
		)

	if method == "tools/list":
		return _result(
			request_id,
			{
				"tools": [
					{"name": t.name, "description": t.description, "inputSchema": t.input_schema}
					for t in registry.get_tools()
				]
			},
		)

	if method == "tools/call":
		name = params.get("name") or ""
		arguments = params.get("arguments") or {}
		try:
			handler = registry.get_tool(name).handler
			return _result(request_id, _text_content(handler(**arguments)))
		except ToolError as exc:
			return _result(request_id, _text_content(str(exc), is_error=True))
		except (frappe.PermissionError, frappe.DoesNotExistError):
			# Deliberately uninformative about existence: saying "no such record"
			# vs "not permitted" would leak whether hidden data exists. Both a
			# hidden record and a genuinely missing one must produce the exact
			# same message. DoesNotExistError is a ValidationError subclass, so
			# this must be caught before the ValidationError branch below.
			return _result(
				request_id,
				_text_content(_("Not permitted for the current ERPNext user."), is_error=True),
			)
		except frappe.ValidationError as exc:
			# Business validation ("Customer is required") is deliberately echoed
			# verbatim: it's exactly what lets the agent correct itself and retry.
			return _result(request_id, _text_content(str(exc), is_error=True))
		except TypeError as exc:
			return _result(request_id, _text_content(_("Bad arguments: {0}").format(exc), is_error=True))
		except Exception:
			# Never surface str(exc) here: it can echo table/constraint names, SQL
			# fragments and file paths to an external agent. Only the logged
			# traceback carries that detail; the caller gets a fixed message.
			frappe.log_error(title="ERPNext AI tool failure", message=frappe.get_traceback())
			return _result(
				request_id,
				_text_content(
					_("An internal error occurred in ERPNext; it has been logged."), is_error=True
				),
			)

	return _error(request_id, METHOD_NOT_FOUND, _("Unknown method: {0}").format(method))


@frappe.whitelist(methods=["POST"])
def handle() -> dict | None:
	"""MCP endpoint. POST JSON-RPC to /api/method/erpnext.ai.mcp.handle.

	The body is read raw rather than through form_dict: JSON-RPC uses a key
	called `method`, which would collide with Frappe's own request kwargs.
	"""
	raw = frappe.request.get_data(as_text=True) if frappe.request else ""
	try:
		payload = json.loads(raw)
	except ValueError:
		# Covers both syntactically invalid JSON and an empty body (empty
		# string is not valid JSON either) — both are parse failures, not
		# silent no-ops.
		return _error(None, PARSE_ERROR, _("Invalid JSON"))

	if not isinstance(payload, dict):
		# MCP 2025-06-18 removed JSON-RPC batching, so a spec-compliant client
		# never sends anything but a single object — not null/number/string/
		# bool, and not a batch array either.
		return _error(None, INVALID_REQUEST, _("Request must be a JSON object"))

	return dispatch(payload)
