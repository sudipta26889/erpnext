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
	scoping.assert_scopable(doctype)

	if not frappe.db.exists("DocType", doctype):
		raise ToolError(_("No such doctype: {0}").format(doctype))

	scoped_filters = scoping.scope_filters(doctype, filters)
	if doctype == "Company":
		# Company carries no `company` field of its own to scope on -- it IS
		# the entity. Scope by identity instead: only companies this agent is
		# bound to, never every legal entity on the site.
		scoped_filters["name"] = ["in", scoping.bound_companies()]

	return frappe.get_list(
		doctype,
		filters=scoped_filters,
		fields=fields or ["name"],
		order_by=order_by or "modified desc",
		limit=clamp_limit(limit),
		offset=max(0, int(start or 0)),
	)


def _assert_company_allowed_for_get_document(company: str) -> None:
	"""get_document's post-read company-scope check, specifically.

	scoping.assert_company_allowed() raises a ToolError naming the company and
	listing what's permitted -- the right, actionable message for
	search_documents/run_report, where the caller is validating a *filter*,
	not confirming a specific record. Here, by the time this runs the caller
	already knows the record exists and is readable (check_permission("read")
	passed above), so that same message would be a distinguishable "yes, but
	wrong company" reply -- still an existence oracle, just a milder one than
	"doesn't exist" vs "not permitted". mcp.py's tools/call handler checks its
	verbatim-ToolError branch before its uniform-message
	(PermissionError, DoesNotExistError) branch, so raising ToolError here
	would leak past the hardening that branch exists to provide. Raise
	frappe.PermissionError instead so this collapses into that same uniform
	branch.
	"""
	try:
		scoping.assert_company_allowed(company)
	except ToolError:
		raise frappe.PermissionError


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
	scoping.assert_scopable(doctype)

	# No frappe.db.exists() pre-check: it is permission-free, so a name that
	# exists but is unreadable would raise a distinguishable ToolError here
	# while a genuinely missing name raises a different one below -- exactly
	# the record-enumeration oracle the MCP transport was hardened to close.
	# frappe.get_doc() raising DoesNotExistError lands in that same uniform
	# branch instead.
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	doc.apply_fieldlevel_read_permissions()

	# The company-scope check is itself a (milder) version of the same
	# oracle, so it must never run before check_permission() above: by the
	# time it runs, the caller already knows the record exists and is
	# readable, so a distinct "wrong company" message leaks nothing new.
	if doctype == "Company":
		# Company carries no `company` field of its own -- it IS the entity.
		_assert_company_allowed_for_get_document(name)
	elif scoping.has_company_field(doctype) and doc.get("company"):
		_assert_company_allowed_for_get_document(doc.company)

	return doc.as_dict(no_default_fields=False)


# --------------------------------------------------------------------------
# Write tools: create, update, submit, cancel, delete.
#
# Documents are always created as DRAFTS -- create_document never submits.
# Drafts are reversible and visible, which is why creation is a free-tier
# operation while submission is not. Approval tiering itself (pausing a
# submit/cancel/delete/call_method call for a human) is enforced by the
# external platform, not here; `tier="approval"` below is advisory metadata
# for that platform's policies and for a human reading the tool catalogue.
# --------------------------------------------------------------------------


def _remember_idempotency(key: str, doctype: str, name: str) -> None:
	frappe.get_doc(
		{
			"doctype": "AI Idempotency Record",
			"idempotency_key": key,
			"ref_doctype": doctype,
			"ref_name": name,
		}
	).insert(ignore_permissions=True)


def _recall_idempotency(key: str) -> dict | None:
	row = frappe.db.get_value(
		"AI Idempotency Record", {"idempotency_key": key}, ["ref_doctype", "ref_name"], as_dict=True
	)
	if not row:
		return None
	if not frappe.db.exists(row.ref_doctype, row.ref_name):
		return None
	return {"doctype": row.ref_doctype, "name": row.ref_name}


def _assert_value_cap(doc) -> None:
	for fieldname in ("grand_total", "base_grand_total", "total"):
		value = doc.get(fieldname)
		if value:
			assert_value_within_cap(value, f"{doc.doctype} {doc.name or ''}".strip())
			return


def _assert_writable_doctype(doctype: str) -> None:
	"""Scoping gate shared by every write tool below, run before any data
	access on `doctype` -- the same two calls search_documents/get_document
	make above.

	Company gets an unconditional block on top. search_documents/get_document
	scope it by identity (bound_companies()), but that is a read-side
	guarantee: it lets an agent see which legal entities it is bound to, not
	mutate the set of them. Creating, editing or deleting a Company is out of
	scope for this tool surface regardless of which company an agent is bound
	to, so it is refused outright here rather than stretching identity-scoping
	into a write guarantee it was never designed to provide.
	"""
	scoping.assert_doctype_scopable(doctype)
	scoping.assert_scopable(doctype)
	if doctype == "Company":
		raise ToolError(_("Company records cannot be created, changed or deleted through this agent."))


def _assert_company_in_scope_or_deny(company: str) -> None:
	"""Write-tool counterpart to _assert_company_allowed_for_get_document above.

	By the time this runs (see _load_for_write) the caller has already passed
	a permission check on this specific document, so it already knows the
	document exists and that it would otherwise be allowed to act on it. A
	distinguishable "wrong company" ToolError at this point would still be a
	milder version of the same existence oracle that helper exists to close,
	so raise frappe.PermissionError instead -- mcp.py's tools/call handler
	collapses that into the same uniform message as a missing or
	permission-denied record.
	"""
	try:
		scoping.assert_company_allowed(company)
	except ToolError:
		raise frappe.PermissionError


def _load_for_write(doctype: str, name: str, ptype: str):
	"""Load a document for a write-family operation (update/submit/cancel/
	delete) and check access, in the oracle-safe order get_document
	establishes above: scoping is asserted before any data access;
	frappe.get_doc() is then a permission-free load that raises
	frappe.DoesNotExistError for a genuinely missing name; check_permission()
	runs immediately after, before anything else (docstatus, value caps,
	company scope) touches the document's content, so an existing-but-
	forbidden record raises frappe.PermissionError at that same point instead
	of a later, distinguishable ToolError leaking that it exists.
	"""
	_assert_writable_doctype(doctype)
	doc = frappe.get_doc(doctype, name)
	doc.check_permission(ptype)
	if scoping.has_company_field(doctype) and doc.get("company"):
		_assert_company_in_scope_or_deny(doc.company)
	return doc


@tool(
	name="create_document",
	description=(
		"Create a record. It is always created as a DRAFT (docstatus 0) and is reversible. "
		"`idempotency_key` is REQUIRED: reusing a key returns the previously created document "
		"instead of creating a duplicate, so retries are safe. Tier: free."
	),
	input_schema={
		"type": "object",
		"properties": {
			"doctype": {"type": "string"},
			"data": {"type": "object"},
			"idempotency_key": {
				"type": "string",
				"description": "Stable unique string for this logical creation, e.g. 'quote-acme-2026-08-06'",
			},
		},
		"required": ["doctype", "data", "idempotency_key"],
		"additionalProperties": False,
	},
)
def create_document(doctype: str, data: dict, idempotency_key: str) -> dict:
	_assert_writable_doctype(doctype)

	existing = _recall_idempotency(idempotency_key)
	if existing:
		return {**existing, "reused": True}

	if not frappe.db.exists("DocType", doctype):
		raise ToolError(_("No such doctype: {0}").format(doctype))

	payload = dict(data or {})
	payload["doctype"] = doctype
	if scoping.has_company_field(doctype):
		if payload.get("company"):
			scoping.assert_company_allowed(payload["company"])
		else:
			payload["company"] = scoping.default_company()

	doc = frappe.get_doc(payload)
	_assert_value_cap(doc)
	doc.insert()
	_remember_idempotency(idempotency_key, doctype, doc.name)
	return {"doctype": doctype, "name": doc.name, "docstatus": doc.docstatus, "reused": False}


@tool(
	name="update_document",
	description=(
		"Change fields on a DRAFT document. Submitted documents are refused here -- cancel and amend "
		"instead. Tier: free for drafts, approval for anything submitted."
	),
	input_schema={
		"type": "object",
		"properties": {
			"doctype": {"type": "string"},
			"name": {"type": "string"},
			"data": {"type": "object"},
		},
		"required": ["doctype", "name", "data"],
		"additionalProperties": False,
	},
)
def update_document(doctype: str, name: str, data: dict) -> dict:
	doc = _load_for_write(doctype, name, "write")
	if doc.docstatus != 0:
		raise ToolError(
			_("{0} {1} is submitted or cancelled; it cannot be edited directly.").format(doctype, name)
		)

	for key, value in (data or {}).items():
		if key == "company":
			scoping.assert_company_allowed(value)
		doc.set(key, value)

	_assert_value_cap(doc)
	doc.save()
	return {"doctype": doctype, "name": doc.name, "docstatus": doc.docstatus}


@tool(
	name="submit_document",
	description=(
		"Submit a draft. This POSTS TO THE GENERAL LEDGER and stock ledger and is not freely "
		"reversible. Tier: approval -- expect this call to pause for a human decision."
	),
	input_schema={
		"type": "object",
		"properties": {"doctype": {"type": "string"}, "name": {"type": "string"}},
		"required": ["doctype", "name"],
		"additionalProperties": False,
	},
	tier="approval",
)
def submit_document(doctype: str, name: str) -> dict:
	doc = _load_for_write(doctype, name, "submit")
	_assert_value_cap(doc)
	doc.submit()
	return {"doctype": doctype, "name": doc.name, "docstatus": doc.docstatus}


@tool(
	name="cancel_document",
	description="Cancel a submitted document, reversing its ledger entries. Tier: approval.",
	input_schema={
		"type": "object",
		"properties": {"doctype": {"type": "string"}, "name": {"type": "string"}},
		"required": ["doctype", "name"],
		"additionalProperties": False,
	},
	tier="approval",
)
def cancel_document(doctype: str, name: str) -> dict:
	doc = _load_for_write(doctype, name, "cancel")
	doc.cancel()
	return {"doctype": doctype, "name": doc.name, "docstatus": doc.docstatus}


@tool(
	name="delete_document",
	description="Permanently delete a document. Tier: approval.",
	input_schema={
		"type": "object",
		"properties": {"doctype": {"type": "string"}, "name": {"type": "string"}},
		"required": ["doctype", "name"],
		"additionalProperties": False,
	},
	tier="approval",
)
def delete_document(doctype: str, name: str) -> dict:
	doc = _load_for_write(doctype, name, "delete")
	frappe.delete_doc(doc.doctype, doc.name)
	return {"doctype": doctype, "name": name, "deleted": True}
