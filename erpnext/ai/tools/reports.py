# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.desk.query_report import run as run_query_report

from erpnext.ai import scoping
from erpnext.ai.registry import ToolError, tool


@tool(
	name="run_report",
	description=(
		"Execute an ERPNext report and return its columns and rows. Use list_reports to discover "
		"names. The operating company is injected into filters automatically. Tier: free."
	),
	input_schema={
		"type": "object",
		"properties": {"report": {"type": "string"}, "filters": {"type": "object"}},
		"required": ["report"],
		"additionalProperties": False,
	},
)
def run_report(report: str, filters: dict | None = None) -> dict:
	if not frappe.db.exists("Report", report):
		raise ToolError(_("No such report: {0}").format(report))

	frappe.get_doc("Report", report).check_permission("read")

	applied = dict(filters or {})
	# Most ERPNext reports take a `company` filter; supply it rather than making
	# the agent guess, and validate any it passed itself.
	if "company" in applied:
		scoping.assert_company_allowed(applied["company"])
	else:
		applied["company"] = scoping.default_company()

	result = run_query_report(report_name=report, filters=applied, ignore_prepared_report=True)
	return {
		"columns": result.get("columns") or [],
		"rows": result.get("result") or [],
		"filters": applied,
	}
