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
INTERNAL_ERROR = -32603
AI_DISABLED = -32001


def _error(request_id: Any, code: int, message: str) -> dict:
	return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, result: dict) -> dict:
	return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _text_content(payload: Any, is_error: bool = False) -> dict:
	text = payload if isinstance(payload, str) else frappe.as_json(payload, indent=None)
	return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _log_deferred(title: str) -> None:
	"""Queue an Error Log write via redis instead of the live transaction, and
	scrub `frappe.local.message_log` so a sanitized response can't leak the
	real message.

	`frappe.throw(msg, exc)` appends `msg` to `frappe.local.message_log`
	*before* raising, and `frappe/utils/response.py::as_json()` unconditionally
	copies a non-empty log into the response as `_server_messages` (v1) /
	`messages` (v2) — regardless of what `result` says. Every caller of this
	function is about to return a deliberately vague `result`, so the real
	message must not also ride along in the same HTTP body. It is not simply
	dropped: captured below before `frappe.clear_messages()` runs, so the
	operator still has it in the Error Log even though the calling agent no
	longer does. The clear happens unconditionally, before the log write is
	even attempted, so it can't be skipped by a logging failure below.

	A plain `frappe.log_error()` does a synchronous `insert()`. If the
	exception being logged came from a database error, the transaction is
	already aborted (this bites hardest on PostgreSQL, which refuses any
	further statement until rollback), so that insert raises too — and the
	*new* exception, carrying whatever the original DB error said, escapes
	uncaught. `defer_insert=True` queues the write instead of touching the
	aborted transaction. The outer try/except is belt-and-braces: failing to
	log must never be able to change what the caller sees.
	"""
	extra = ""
	if frappe.local.message_log:
		logged = "\n".join(frappe.as_json(entry, indent=None) for entry in frappe.get_message_log())
		extra = f"\n\nmessage_log (cleared before response):\n{logged}"
		frappe.clear_messages()
	try:
		frappe.log_error(title=title, message=frappe.get_traceback() + extra, defer_insert=True)
	except Exception:
		pass


def dispatch(payload: Any) -> dict | None:
	"""Handle one JSON-RPC request. Returns None for notifications.

	`dispatch()` is a published interface in its own right, called directly by
	tests and potentially other callers, not only reached through `handle()`
	— so the payload-shape guard lives here rather than only in `handle()`.
	Beyond that, every method branch below is covered by a single top-level
	guard: no branch may escape as an unhandled exception, so every path
	returns a well-formed JSON-RPC response, never a raw 500.
	"""
	if not isinstance(payload, dict):
		# MCP 2025-06-18 removed JSON-RPC batching, so a spec-compliant client
		# never sends anything but a single object — not null/number/string/
		# bool, and not a batch array either.
		return _error(None, INVALID_REQUEST, _("Request must be a JSON object"))

	request_id = payload.get("id")
	method = payload.get("method")

	if request_id is None:
		# Notification: acknowledge by doing nothing. Answering would violate JSON-RPC.
		return None

	if not method:
		return _error(request_id, INVALID_REQUEST, _("Missing method"))

	try:
		return _route(request_id, method, payload.get("params") or {})
	except ToolError as exc:
		# A protocol-level branch (not tools/call, which converts its own
		# ToolErrors into a result+isError below) raised — e.g. malformed
		# `enabled_tools` surfacing from tools/list, or from the `enabled`
		# check. Still a JSON-RPC error object, never an unhandled exception.
		return _error(request_id, exc.code, str(exc))
	except Exception:
		_log_deferred("ERPNext AI dispatch failure")
		return _error(
			request_id, INTERNAL_ERROR, _("An internal error occurred in ERPNext; it has been logged.")
		)


def _route(request_id: Any, method: str, params: dict) -> dict:
	if not registry.settings().enabled:
		return _error(request_id, AI_DISABLED, _("ERPNext AI is disabled in AI Settings."))

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
			# Document.load_from_db and Frappe's permission checks both raise via
			# frappe.throw(), which populates message_log with the very detail
			# ("Sales Invoice ABC-001 not found") this branch exists to hide —
			# _log_deferred() clears it so it doesn't ride along in the same
			# response as _server_messages / messages.
			_log_deferred("ERPNext AI tool permission or missing record")
			return _result(
				request_id,
				_text_content(_("Not permitted for the current ERPNext user."), is_error=True),
			)
		except frappe.ValidationError as exc:
			# Business validation ("Customer is required") is deliberately echoed
			# verbatim: it's exactly what lets the agent correct itself and retry.
			# frappe.local.message_log is deliberately left untouched here too —
			# it holds the same text already going out in `result`, not anything
			# the agent doesn't already have.
			return _result(request_id, _text_content(str(exc), is_error=True))
		except TypeError:
			# Catches a TypeError raised *inside* a handler too, not only a
			# caller argument mismatch — str(exc) can name internal callables,
			# so (like the generic branch below) this is logged and given a
			# fixed message rather than echoed verbatim.
			_log_deferred("ERPNext AI tool bad arguments")
			return _result(
				request_id,
				_text_content(
					_("Bad arguments for this tool call. Check the tool's input schema and retry."),
					is_error=True,
				),
			)
		except Exception:
			# Never surface str(exc) here: it can echo table/constraint names, SQL
			# fragments and file paths to an external agent. Only the logged
			# traceback carries that detail; the caller gets a fixed message.
			_log_deferred("ERPNext AI tool failure")
			return _result(
				request_id,
				_text_content(_("An internal error occurred in ERPNext; it has been logged."), is_error=True),
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

	return dispatch(payload)
