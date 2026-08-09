# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from erpnext.ai.registry import ToolError, _json_list, settings, tool


def echo(value: str) -> str:
	"""Trivial allowlistable method, used to prove the allowlist works."""
	return value


@tool(
	name="call_method",
	description=(
		"Call a specific ERPNext server method by dotted path. Only paths explicitly listed in "
		"AI Settings are permitted. Tier: approval."
	),
	input_schema={
		"type": "object",
		"properties": {"method": {"type": "string"}, "args": {"type": "object"}},
		"required": ["method"],
		"additionalProperties": False,
	},
	tier="approval",
)
def call_method(method: str, args: dict | None = None) -> object:
	# Empty means deny-all here -- the opposite polarity to enabled_tools,
	# where empty means "all tools". A method allowlist must fail closed.
	# _json_list() itself raises ToolError on malformed JSON rather than
	# returning [], so a misconfigured allowlist fails closed too.
	allowed = _json_list(settings().allowed_methods, _("Allowed Methods"))
	if method not in allowed:
		raise ToolError(_("Method {0} is not in the AI Settings allowlist.").format(method))

	return frappe.get_attr(method)(**(args or {}))
