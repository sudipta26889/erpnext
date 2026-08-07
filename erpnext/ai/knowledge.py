# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Facts about this ERPNext install, generated live so they cannot go stale."""

import frappe
from frappe import _

from erpnext.ai import registry


def orientation_text() -> str:
	"""System instructions handed to the agent on every MCP connection."""
	company = registry.settings().erpnext_company
	# frappe.db.get_value("Company", None, ...) does not mean "no match": it
	# returns an arbitrary company's row, which would leak another company's
	# currency and country into the orientation text. Only look up a company
	# when one is actually bound.
	details = (
		company and frappe.db.get_value("Company", company, ["default_currency", "country"], as_dict=True)
	) or frappe._dict()

	return _(
		"You are connected to ERPNext {version}, an ERP system, over MCP.\n\n"
		"Operating company: {company} (currency {currency}, country {country}). "
		"Unless a tool argument says otherwise, every query is scoped to this company; "
		"requesting a different company is refused rather than silently re-scoped.\n\n"
		"Document lifecycle: documents are created as DRAFTS (docstatus 0) and are freely "
		"reversible. SUBMIT (docstatus 1) posts to the general ledger and stock ledger and is "
		"not freely reversible. Tools marked 'Tier: approval' therefore pause for a human "
		"decision before they run — expect that and do not retry them in a loop.\n\n"
		"Start with get_company_context to ground yourself, search_doctypes to find the right "
		"record type, and describe_doctype to learn its fields before writing anything. "
		"All permissions are enforced by ERPNext for the connected user."
	).format(
		version=frappe.__version__,
		company=company or _("not configured"),
		currency=details.get("default_currency") or _("unknown"),
		country=details.get("country") or _("unknown"),
	)


def workspace_map() -> dict[str, dict]:
	"""Doctype -> {workspace, module, url}.

	Answers "where is what" from this install's own Workspace links, so it
	reflects customisations that no published manual ever will. Restricted to
	workspaces the current user can actually see: frappe.get_all() reads
	Workspace Link -- a child table of Workspace -- with ignore_permissions=True,
	so without this filter another user's private workspace layout would leak
	to everyone.
	"""
	visible_workspaces = frappe.get_all(
		"Workspace",
		or_filters={"public": 1, "for_user": frappe.session.user},
		pluck="name",
	)
	links = frappe.get_all(
		"Workspace Link",
		filters={
			"link_type": "DocType",
			"type": "Link",
			"parent": ["in", visible_workspaces],
		},
		fields=["link_to", "parent", "label"],
		# Dedup below is "first hit wins" -- must be deterministic, not
		# whatever order the DB happens to return rows in.
		order_by="parent asc, idx asc",
	)
	mapping: dict[str, dict] = {}
	for link in links:
		if not link.link_to or link.link_to in mapping:
			continue
		mapping[link.link_to] = {
			"workspace": link.parent,
			"module": frappe.db.get_value("DocType", link.link_to, "module"),
			"url": desk_url(link.link_to),
		}
	return mapping


def desk_url(doctype: str) -> str:
	return "/app/" + frappe.scrub(doctype).replace("_", "-")
