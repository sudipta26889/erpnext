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
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import frappe
from frappe import _

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
	tags: list[str] = field(default_factory=list)


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


@lru_cache(maxsize=1)
def settings():
	return frappe.get_single("AI Settings")


def _json_list(raw: str | None) -> list[str]:
	raw = (raw or "").strip()
	if not raw:
		return []
	try:
		value = json.loads(raw)
	except ValueError:
		return []
	return [item for item in value if isinstance(item, str)]


def get_tools() -> list[Tool]:
	"""Registered tools filtered by the AI Settings allowlist (empty means all)."""
	_load_tool_modules()
	allow = _json_list(settings().enabled_tools)
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


def assert_value_within_cap(value: float, label: str) -> None:
	cap = float(settings().max_document_value or 0)
	if cap and float(value or 0) > cap:
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
