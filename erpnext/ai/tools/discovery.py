# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Grounding and discovery tools.

ERPNext is self-describing, so these read live metadata rather than a scraped
document that would drift from the running install.
"""

import frappe
from frappe import _

from erpnext.ai import scoping
from erpnext.ai.knowledge import desk_url, workspace_map
from erpnext.ai.registry import ToolError, clamp_limit, tool

_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}

# Doctypes counted by get_company_context() that carry no `company` field on
# this schema, so there is nothing to scope the count by -- left as a
# site-wide total rather than silently pretending to be scoped. Deliberate,
# not an oversight: contrast with Sales Order / Sales Invoice below, which do
# carry `company` and are scoped.
_UNSCOPED_COUNT_DOCTYPES = frozenset({"Customer", "Supplier", "Item"})


def _escape_like(text: str) -> str:
	"""Escape LIKE metacharacters so `%`/`_` in user input can't act as wildcards.

	PostgreSQL's default LIKE escape character is backslash, so this needs no
	accompanying ESCAPE clause.
	"""
	return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@tool(
	name="get_company_context",
	description=(
		"Ground yourself: returns the operating company, its currency, country, fiscal year, "
		"chart-of-accounts roots, active modules and headline record counts. "
		"Call this first in any new conversation. Tier: free."
	),
	input_schema=_NO_ARGS,
)
def get_company_context() -> dict:
	company = scoping.default_company()
	details = (
		frappe.db.get_value("Company", company, ["default_currency", "country", "abbr"], as_dict=True)
		or frappe._dict()
	)
	fiscal_year = frappe.get_all(
		"Fiscal Year",
		fields=["name", "year_start_date", "year_end_date"],
		order_by="year_start_date desc",
		limit=1,
	)
	roots = frappe.get_all(
		"Account",
		filters={"company": company, "parent_account": ["in", [None, ""]]},
		fields=["name", "root_type"],
	)
	return {
		"company": company,
		"abbreviation": details.get("abbr"),
		"currency": details.get("default_currency"),
		"country": details.get("country"),
		"fiscal_year": fiscal_year[0] if fiscal_year else None,
		"chart_of_accounts_roots": roots,
		"modules": frappe.get_all("Module Def", filters={"app_name": "erpnext"}, pluck="name"),
		"accessible_companies": scoping.bound_companies(),
		"counts": {
			doctype: (
				frappe.db.count(doctype)
				if doctype in _UNSCOPED_COUNT_DOCTYPES
				else frappe.db.count(doctype, {"company": ["in", scoping.bound_companies()]})
			)
			for doctype in ("Customer", "Supplier", "Item", "Sales Order", "Sales Invoice")
		},
	}


@tool(
	name="search_doctypes",
	description=(
		"Find ERPNext record types by name or purpose. Returns each doctype with its module, "
		"owning workspace and desk URL, so you can also answer 'where is that in the UI'. Tier: free."
	),
	input_schema={
		"type": "object",
		"properties": {
			"query": {"type": "string", "description": "Free text, e.g. 'overdue invoice' or 'stock entry'"},
			"module": {"type": "string", "description": "Optional module filter, e.g. 'Accounts'"},
			"limit": {"type": "integer"},
		},
		"required": ["query"],
		"additionalProperties": False,
	},
)
def search_doctypes(query: str, module: str | None = None, limit: int | None = None) -> list[dict]:
	filters: dict = {"istable": 0}
	if module:
		filters["module"] = module

	pattern = f"%{_escape_like(query)}%"
	rows = frappe.get_all(
		"DocType",
		filters=filters,
		or_filters={"name": ["like", pattern], "description": ["like", pattern]},
		fields=["name", "module", "description", "issingle", "is_submittable"],
		limit=clamp_limit(limit),
	)
	# frappe.get_all() reads with ignore_permissions=True, so filter what gets
	# advertised down to what the caller can actually read -- discovery must
	# never surface a doctype the caller has no access to. frappe.has_permission()
	# defaults `throw` to False, which it forwards as `print_logs=False`, so a
	# denial here doesn't also msgprint into the response's _server_messages.
	rows = [row for row in rows if frappe.has_permission(row.name, "read")]
	locations = workspace_map()
	return [
		{
			"doctype": row.name,
			"module": row.module,
			"description": row.description,
			"is_single": bool(row.issingle),
			"is_submittable": bool(row.is_submittable),
			"workspace": (locations.get(row.name) or {}).get("workspace"),
			"url": desk_url(row.name),
		}
		for row in rows
	]


@tool(
	name="describe_doctype",
	description=(
		"Full schema for one doctype: fields with types and options, link targets, mandatory flags, "
		"naming, whether it is submittable, and YOUR effective permissions on it. "
		"Read this before creating or updating records. Tier: free."
	),
	input_schema={
		"type": "object",
		"properties": {"doctype": {"type": "string"}},
		"required": ["doctype"],
		"additionalProperties": False,
	},
)
def describe_doctype(doctype: str) -> dict:
	if not frappe.db.exists("DocType", doctype):
		raise ToolError(_("No such doctype: {0}").format(doctype))

	# Same message as the nonexistent-doctype branch above, deliberately: this
	# tool must not be usable to enumerate doctypes the caller cannot see by
	# distinguishing "doesn't exist" from "exists but you can't read it".
	if not frappe.has_permission(doctype, "read"):
		raise ToolError(_("No such doctype: {0}").format(doctype))

	meta = frappe.get_meta(doctype)
	fields = [
		{
			"fieldname": f.fieldname,
			"label": f.label,
			"fieldtype": f.fieldtype,
			"options": f.options,
			"reqd": bool(f.reqd),
			"read_only": bool(f.read_only),
			"default": f.default,
		}
		for f in meta.fields
		if f.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML")
	]
	return {
		"doctype": doctype,
		"module": meta.module,
		"is_submittable": bool(meta.is_submittable),
		"is_single": bool(meta.issingle),
		"is_tree": bool(meta.get("is_tree")),
		"autoname": meta.autoname,
		"title_field": meta.title_field,
		"fields": fields,
		"child_tables": [
			{"fieldname": f.fieldname, "doctype": f.options}
			for f in meta.fields
			if f.fieldtype in ("Table", "Table MultiSelect")
		],
		"permissions": [
			action
			for action in ("read", "write", "create", "delete", "submit", "cancel")
			if frappe.has_permission(doctype, ptype=action)
		],
		"url": desk_url(doctype),
	}


@tool(
	name="list_reports",
	description="List available ERPNext reports, optionally filtered by module. Tier: free.",
	input_schema={
		"type": "object",
		"properties": {"module": {"type": "string"}, "limit": {"type": "integer"}},
		"additionalProperties": False,
	},
)
def list_reports(module: str | None = None, limit: int | None = None) -> list[dict]:
	filters = {"disabled": 0}
	if module:
		filters["module"] = module
	rows = frappe.get_all(
		"Report",
		filters=filters,
		fields=["name", "module", "report_type", "ref_doctype"],
		limit=clamp_limit(limit),
	)
	# frappe.get_all() reads with ignore_permissions=True, so filter what gets
	# advertised down to reports the caller can actually read -- discovery
	# must never advertise a report run_report would then refuse to run.
	return [row for row in rows if frappe.has_permission("Report", "read", row.name)]


@tool(
	name="get_workspace_map",
	description=(
		"Map of doctype -> {workspace, module, desk URL} for this install. "
		"Use to answer 'where do I do X in the UI'. Tier: free."
	),
	input_schema=_NO_ARGS,
)
def get_workspace_map() -> dict:
	return workspace_map()
