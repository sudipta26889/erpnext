# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Company scoping.

`company` is a mandatory filter on nearly every transactional doctype. An agent
that does not know which company it operates in produces ambiguous queries, and
on a multi-entity site would mix the books of separate legal entities.
"""

import frappe
from frappe import _

from erpnext.ai import registry
from erpnext.ai.registry import ToolError


def default_company() -> str:
	company = registry.settings().erpnext_company
	if not company:
		raise ToolError(_("No ERPNext Company is bound in AI Settings."))
	return company


def bound_companies() -> list[str]:
	settings = registry.settings()
	companies = [settings.erpnext_company] if settings.erpnext_company else []
	companies += [row.company for row in (settings.additional_companies or []) if row.company]
	return companies


def assert_company_allowed(company: str) -> None:
	allowed = bound_companies()
	if company not in allowed:
		# Refusing is deliberate. An agent reaching for the wrong legal entity is
		# a bug worth surfacing, not something to quietly correct.
		raise ToolError(
			_("Company {0} is out of scope. This agent may only access: {1}.").format(
				company, ", ".join(allowed) or _("none")
			)
		)


def has_company_field(doctype: str) -> bool:
	return bool(frappe.get_meta(doctype).get_field("company"))


def scope_filters(doctype: str, filters: dict | None) -> dict:
	"""Return filters with the bound company applied, validating any explicit one."""
	result = dict(filters or {})
	if not has_company_field(doctype):
		return result

	explicit = result.get("company")
	if explicit:
		if isinstance(explicit, str):
			assert_company_allowed(explicit)
		return result

	result["company"] = default_company()
	return result
