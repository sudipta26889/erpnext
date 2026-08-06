# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Generic document tools.

ERPNext's surface is uniform -- every doctype is CRUD plus optional
submit/cancel -- so these are generic and metadata-driven rather than
hand-written per doctype.
"""

import frappe
from frappe import _

from erpnext.ai import scoping
from erpnext.ai.registry import ToolError, assert_value_within_cap, clamp_limit, tool


@tool(
	name="search_documents",
	description=(
		"List records of a doctype. `filters` uses Frappe syntax: {'status': 'Draft'} or "
		"{'grand_total': ['>', 1000]}. Automatically scoped to the operating company. Tier: free."
	),
	input_schema={
		"type": "object",
		"properties": {
			"doctype": {"type": "string"},
			"filters": {"type": "object"},
			"fields": {"type": "array", "items": {"type": "string"}},
			"order_by": {"type": "string"},
			"limit": {"type": "integer"},
			"start": {"type": "integer"},
		},
		"required": ["doctype"],
		"additionalProperties": False,
	},
)
def search_documents(
	doctype: str,
	filters: dict | None = None,
	fields: list[str] | None = None,
	order_by: str | None = None,
	limit: int | None = None,
	start: int | None = None,
) -> list[dict]:
	# Must run before any query touches this doctype's table: some doctypes
	# (Version, Deleted Document, ...) carry no `company` field but can still
	# expose the contents of documents belonging to any company.
	scoping.assert_doctype_scopable(doctype)

	if not frappe.db.exists("DocType", doctype):
		raise ToolError(_("No such doctype: {0}").format(doctype))

	return frappe.get_list(
		doctype,
		filters=scoping.scope_filters(doctype, filters),
		fields=fields or ["name"],
		order_by=order_by or "modified desc",
		limit=clamp_limit(limit),
		offset=start or 0,
	)


@tool(
	name="get_document",
	description="Fetch one document in full, including its child tables. Tier: free.",
	input_schema={
		"type": "object",
		"properties": {"doctype": {"type": "string"}, "name": {"type": "string"}},
		"required": ["doctype", "name"],
		"additionalProperties": False,
	},
)
def get_document(doctype: str, name: str) -> dict:
	# Same "before touching any data" guarantee as search_documents above.
	scoping.assert_doctype_scopable(doctype)

	if not frappe.db.exists(doctype, name):
		raise ToolError(_("{0} {1} not found.").format(doctype, name))

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	if scoping.has_company_field(doctype) and doc.get("company"):
		scoping.assert_company_allowed(doc.company)
	return doc.as_dict(no_default_fields=False)
