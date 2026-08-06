# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Tool registration plus the ERPNext-side safety caps.

Approval tiering is enforced by Paperclip's tool policies, not here. What lives
in this module is the second line of defence: an allowlist and blast-radius
caps that hold even if a policy is misconfigured or a different agent is
pointed at the MCP endpoint.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document

# JSON-RPC application error code. -32000 is the generic "server error" slot.
TOOL_ERROR_CODE = -32000


class ToolError(Exception):
	"""Raised when a tool cannot run. Surfaced to the agent as a readable message."""

	def __init__(self, message: str, code: int = TOOL_ERROR_CODE):
		super().__init__(message)
		self.code = code


@dataclass
class Tool:
	name: str
	description: str
	input_schema: dict
	handler: Callable[..., Any]
	tier: str = "free"


_REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, input_schema: dict, tier: str = "free") -> Callable:
	"""Register a callable as an MCP tool.

	`tier` is advisory metadata surfaced in the tool description so Paperclip
	policies (and a human reading the catalogue) can see which calls are
	expected to require approval.
	"""

	def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
		_REGISTRY[name] = Tool(
			name=name, description=description, input_schema=input_schema, handler=fn, tier=tier
		)
		return fn

	return decorator


# Invalidated by AISettings.on_update so long-lived workers pick up admin
# changes (kill-switch, tightened caps) without a restart. See
# erpnext/ai/doctype/ai_settings/ai_settings.py.
@lru_cache(maxsize=1)
def settings() -> Document:
	return frappe.get_single("AI Settings")


def _json_list(raw: str | None, field_label: str) -> list[str]:
	"""Parse a Small Text JSON-list-of-strings field.

	A blank field is genuinely empty and returns `[]` (for `enabled_tools`
	that means "all tools", by design). Anything else that isn't a JSON list
	of strings — including a value that fails to parse at all — raises so
	callers fail closed instead of silently exposing everything.
	"""
	raw = (raw or "").strip()
	if not raw:
		return []
	try:
		value = json.loads(raw)
	except ValueError:
		raise ToolError(_("{0} is not valid JSON.").format(field_label)) from None
	if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
		raise ToolError(_("{0} must be a JSON list of strings.").format(field_label))
	return value


def get_tools() -> list[Tool]:
	"""Registered tools filtered by the AI Settings allowlist (empty means all)."""
	_load_tool_modules()
	allow = _json_list(settings().enabled_tools, _("Enabled Tools"))
	tools = list(_REGISTRY.values())
	if allow:
		tools = [t for t in tools if t.name in allow]
	return sorted(tools, key=lambda t: t.name)


def get_tool(name: str) -> Tool:
	for candidate in get_tools():
		if candidate.name == name:
			return candidate
	raise ToolError(_("Unknown or disabled tool: {0}").format(name))


def clamp_limit(requested: int | None) -> int:
	cap = int(settings().max_batch_size or 20)
	if not requested or requested < 1:
		return cap
	return min(int(requested), cap)


def assert_value_within_cap(value: float | None, label: str) -> None:
	cap = float(settings().max_document_value or 0)
	if not cap:
		return  # 0 means unlimited: nothing to enforce, None passes harmlessly.
	if value is None:
		# A caller whose monetary lookup came back None must not be waved through
		# uncapped just because `float(None or 0)` used to coerce to 0.
		raise ToolError(
			_("{0} value could not be determined, so the configured AI limit of {1} cannot be enforced.").format(
				label, cap
			)
		)
	if float(value) > cap:
		# Refuse rather than trim: a silently shrunk document is worse than an error.
		raise ToolError(
			_("{0} value {1} exceeds the configured AI limit of {2}.").format(label, value, cap)
		)


def _load_tool_modules() -> None:
	"""Import tool modules so their decorators run. Idempotent."""
	from erpnext.ai.tools import discovery, documents, methods, reports  # noqa: F401


@tool(
	name="ping",
	description="Health check. Returns pong and the ERPNext site name. Tier: free.",
	input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def ping() -> dict:
	return {"pong": True, "site": frappe.local.site}
