# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.desk.query_report import run as run_query_report

from erpnext.ai import scoping
from erpnext.ai.registry import ToolError, clamp_limit, tool


@tool(
	name="run_report",
	description=(
		"Execute an ERPNext report and return its columns and rows, capped to a limit and marked "
		"'truncated' if the report has more. Use list_reports to discover names. The company filter "
		"is passed to the report and honoured only if that report supports it -- results are NOT "
		"automatically scoped to one company. Refused on a site with more than one Company: reports "
		"cannot be reliably restricted to one entity and must be vetted individually before use. "
		"Tier: free."
	),
	input_schema={
		"type": "object",
		"properties": {"report": {"type": "string"}, "filters": {"type": "object"}},
		"required": ["report"],
		"additionalProperties": False,
	},
)
def run_report(report: str, filters: dict | None = None) -> dict:
	if scoping.is_multi_company_site():
		# Frappe silently drops filter keys a report does not declare, and many
		# ERPNext reports never reference `company` at all; Script Reports are
		# arbitrary Python. Passing `company` below is a hint, not a control --
		# on a single-company site that's harmless (nothing to leak between),
		# but on a multi-company site it would be a false promise of scoping.
		raise ToolError(
			_(
				"run_report is unavailable on a multi-company site: reports cannot be reliably "
				"restricted to one company, so each report would need to be vetted individually "
				"before this agent could safely use it."
			)
		)

	if not frappe.db.exists("Report", report):
		raise ToolError(_("No such report: {0}").format(report))

	# get_report_doc() inside run_query_report() below already enforces both
	# the Report's own permitted roles (doc.is_permitted()) and
	# frappe.has_permission(ref_doctype, "report") -- strictly stronger than a
	# bare check_permission("read") on the Report doctype, which is all a
	# separate check here would add.
	if filters is not None and not isinstance(filters, dict):
		# Mirrors scope_filters()'s shape guard: Frappe's list filter form
		# would otherwise reach dict(filters or {}) below and raise a bare
		# ValueError, escaping this security-adjacent function uncaught.
		raise ToolError(
			_("Filters for report {0} must be a dict of field: value pairs, not a {1}.").format(
				report, type(filters).__name__
			)
		)

	applied = dict(filters or {})
	# Most ERPNext reports take a `company` filter; supply it rather than making
	# the agent guess, and validate any it passed itself.
	if "company" in applied:
		scoping.assert_company_allowed(applied["company"])
	else:
		applied["company"] = scoping.default_company()

	result = run_query_report(report_name=report, filters=applied, ignore_prepared_report=True)
	rows = result.get("result") or []
	cap = clamp_limit(None)
	return {
		"columns": result.get("columns") or [],
		"rows": rows[:cap],
		"filters": applied,
		"truncated": len(rows) > cap,
		"total_rows": len(rows),
	}
