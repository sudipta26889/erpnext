# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Company scoping.

`company` is a mandatory filter on nearly every transactional doctype. An agent
that does not know which company it operates in produces ambiguous queries, and
on a multi-entity site would mix the books of separate legal entities.

The company guarantee this module provides only holds for doctypes that carry
a `company` field — see `has_company_field()`. A company-less doctype is not
touched by anything here: `scope_filters()` simply returns its filters
unchanged, so any doctype whose documents can reveal another company's data
without a `company` field of its own must be explicitly vetted before it is
exposed to a tool. See `assert_doctype_scopable()` for the unconditional
deny-list of such doctypes, and `assert_scopable()` for the complementary,
conditional guard: it lets a company-less-but-otherwise-harmless doctype
through on today's single-company site, and refuses it the moment a second
Company exists, since it can no longer be restricted to the bound entity.
"""

from typing import Any

import frappe
from frappe import _

from erpnext.ai import registry
from erpnext.ai.registry import ToolError

# Doctypes that carry no `company` field but whose documents can expose the
# contents of records belonging to any company — e.g. Version stores
# field-level before/after diffs of arbitrary documents, and Comment/
# Communication/File can be attached to a document in any company. Deleted
# Document stores the full JSON of any deleted document (any company, any
# doctype) and Prepared Report stores rendered report output, so both belong
# here for the same reason. Integration Request, Notification Log and ToDo
# likewise carry payloads/content tied to documents in any company without a
# `company` field of their own. Not wired into scope_filters() or anything
# else yet; a later task's read tools call assert_doctype_scopable() before
# touching one of these.
COMPANY_LESS_SENSITIVE_DOCTYPES: frozenset[str] = frozenset(
	{
		"Version",
		"Comment",
		"Communication",
		"File",
		"Activity Log",
		"Email Queue",
		"Error Log",
		"Access Log",
		"Deleted Document",
		"Prepared Report",
		"Integration Request",
		"Notification Log",
		"ToDo",
	}
)


def assert_doctype_scopable(doctype: str) -> None:
	"""Raise if `doctype` cannot be company-scoped and so is unsafe to expose.

	Covers the doctypes in `COMPANY_LESS_SENSITIVE_DOCTYPES`: they have no
	`company` field for `scope_filters()` to enforce, but can still carry the
	contents of documents belonging to any company.
	"""
	if doctype in COMPANY_LESS_SENSITIVE_DOCTYPES:
		raise ToolError(
			_("{0} cannot be scoped to a company and is not available to this agent.").format(doctype)
		)


# Doctypes that must never be created, changed or deleted through the write
# tools, on any site -- company scoping is irrelevant here because the power
# these carry has nothing to do with which company's books are touched. Most
# have no `company` field at all, so they would otherwise sail through
# `assert_scopable()` unchallenged on a single-company site.
#
# Two families:
#  - Privilege escalation: User, Role, Role Profile, Custom DocPerm, DocPerm
#    can grant the agent (or any account) more access than it was scoped to.
#  - Code / behaviour injection: Property Setter, Server Script, Client
#    Script, DocType, Custom Field, Workflow, Workflow Action, Webhook,
#    Scheduled Job Type can change what code runs or how existing doctypes
#    behave -- `call_method` is allowlist-only specifically to keep arbitrary
#    code execution out of this tool surface; creating a Server Script
#    through `create_document` would walk straight around that allowlist.
#
# `AI Settings` belongs here for a reason specific to this feature: it holds
# `max_document_value`, `allowed_methods`, `enabled_tools` and `allowed_roles`
# -- the agent must never be able to widen its own caps or allowlists by
# editing the document that defines them.
WRITE_FORBIDDEN_DOCTYPES: frozenset[str] = frozenset(
	{
		"User",
		"Role",
		"Role Profile",
		"Custom DocPerm",
		"DocPerm",
		"Property Setter",
		"Server Script",
		"Client Script",
		"DocType",
		"Custom Field",
		"Workflow",
		"Workflow Action",
		"Webhook",
		"Scheduled Job Type",
		"AI Settings",
	}
)


def assert_write_allowed_doctype(doctype: str) -> None:
	"""Raise if `doctype` is outright forbidden for create/update/delete.

	Called by every write tool before any data access, alongside
	`assert_doctype_scopable`/`assert_scopable` -- this is a separate guard
	because the doctypes in `WRITE_FORBIDDEN_DOCTYPES` are refused regardless
	of company scoping, not because they can't be scoped.
	"""
	if doctype in WRITE_FORBIDDEN_DOCTYPES:
		raise ToolError(_("{0} cannot be created, changed or deleted through this agent.").format(doctype))


def default_company() -> str:
	company = registry.settings().erpnext_company
	if not company:
		raise ToolError(_("No ERPNext Company is bound in AI Settings."))
	return company


def bound_companies() -> list[str]:
	settings = registry.settings()
	if not settings.erpnext_company:
		# Consistent with default_company(): a blank primary company means the
		# configuration is incomplete, so the whole scope fails closed rather
		# than falling back to whatever additional_companies happens to hold.
		return []
	companies = [settings.erpnext_company]
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


def is_multi_company_site() -> bool:
	"""True once a second Company exists.

	Company scoping below relies on either a `company` field to filter on, or
	(for `Company` itself) identity. A doctype with neither is safe to expose
	unscoped only because this site currently has one Company -- nothing to
	leak between. `assert_scopable()` uses this to fail closed the moment
	that stops being true, instead of silently leaking across entities.
	"""
	return frappe.db.count("Company") > 1


def assert_scopable(doctype: str) -> None:
	"""Raise if `doctype` cannot be restricted to the bound company/companies.

	A doctype with a `company` field is scoped by `scope_filters()`. `Company`
	itself carries no `company` field but is scoped by identity instead (see
	`erpnext/ai/tools/documents.py`), so it is always scopable. Everything
	else with no `company` field cannot be restricted at all -- fine on the
	single-company site this guarantee otherwise assumes throughout this
	module, but a leak waiting to happen the moment a second Company exists.
	"""
	if doctype == "Company" or has_company_field(doctype):
		return
	if is_multi_company_site():
		raise ToolError(
			_(
				"{0} carries no `company` field, so it cannot be restricted to the bound "
				"entity. This site has more than one Company, so this doctype is not "
				"available to this agent."
			).format(doctype)
		)


def _reject_company_filter(explicit: Any) -> None:
	# Refusing unrecognised/unsafe filter shapes is deliberate: `!=`, `not in`,
	# `like`, etc. can select entities outside the bound scope, and there is no
	# safe way to interpret them here.
	allowed = ", ".join(bound_companies()) or _("none")
	raise ToolError(
		_(
			"Invalid company filter {0}. The company filter must be a company name "
			"or an '=' / 'in' filter over permitted companies: {1}."
		).format(explicit, allowed)
	)


def _assert_company_filter_allowed(explicit: Any) -> None:
	if isinstance(explicit, str):
		assert_company_allowed(explicit)
		return

	if isinstance(explicit, list | tuple) and len(explicit) == 2:
		operator, value = explicit
		# Frappe normalises filter operators case-insensitively (["IN", [...]]
		# is legal to Frappe), so match the same way — otherwise a caller using
		# the case Frappe itself accepts is refused here for no security reason.
		if isinstance(operator, str):
			operator = operator.strip().lower()
		if operator in ("=", "=="):
			if not isinstance(value, str):
				_reject_company_filter(explicit)
			assert_company_allowed(value)
			return
		if operator == "in":
			if not isinstance(value, list | tuple) or not value:
				_reject_company_filter(explicit)
			for company in value:
				assert_company_allowed(company)
			return

	_reject_company_filter(explicit)


def scope_filters(doctype: str, filters: dict | None) -> dict:
	"""Return filters with the bound company applied, validating any explicit one."""
	if filters is not None and not isinstance(filters, dict):
		# Frappe's list filter form (`[["company", "=", "X"]]`) would otherwise
		# reach `dict(filters or {})` below and raise a bare ValueError — or,
		# for the 2-element form, silently coerce into a dict via dict()'s
		# iterable-of-pairs behaviour. Both must fail closed as a ToolError,
		# not escape this security function as something else.
		raise ToolError(
			_("Filters for {0} must be a dict of field: value pairs, not a {1}.").format(
				doctype, type(filters).__name__
			)
		)
	result = dict(filters or {})
	if not has_company_field(doctype):
		return result

	explicit = result.get("company")
	if explicit:
		_assert_company_filter_allowed(explicit)
		return result

	result["company"] = default_company()
	return result
