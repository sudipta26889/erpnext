# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Generic document tools.

ERPNext's surface is uniform -- every doctype is CRUD plus optional
submit/cancel -- so these are generic and metadata-driven rather than
hand-written per doctype.
"""

import frappe
from frappe import _
from frappe.model import default_fields

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
	if not frappe.has_permission(doctype, "read"):
		# Same message as the nonexistent-doctype branch above, deliberately:
		# frappe.get_list() below would otherwise raise its own distinguishable
		# frappe.PermissionError for an existing-but-unreadable doctype, which
		# mcp.py's tools/call handler collapses into a *different* uniform
		# "Not permitted..." message than the ToolError branch echoes verbatim
		# -- letting a caller enumerate hidden custom doctypes by name. Mirrors
		# describe_doctype's identical pattern.
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


_IDEMPOTENCY_SAVEPOINT = "ai_idem"


def _remember_idempotency(key: str, doctype: str, name: str) -> None:
	"""Store key -> (doctype, name), tolerant of a record already existing.

	A record can already exist under this key for two reasons, both of which
	must end with the *new* document winning rather than an error:

	1. The document a previous call created under this key was since deleted.
	   `_recall_idempotency` then returns None (nothing to reuse) and a new
	   document gets created for the same key -- but the record row's `name`
	   *is* the key (autoname is `field:idempotency_key`), so a plain
	   `insert()` collides with the surviving row and raises
	   `DuplicateEntryError`. On PostgreSQL that aborts the whole transaction,
	   rolling back the document `doc.insert()` just committed earlier in
	   this same transaction and permanently poisoning the key -- every
	   future call would hit the identical collision.
	2. A genuine concurrent double-call: two callers race past
	   `_recall_idempotency` (neither sees the other's row yet), both create
	   a document, and whichever reaches `insert()` here second loses the
	   race on the same unique key.

	Either way the fix is the same: catch the collision inside its own
	savepoint (so the abort only unwinds this insert attempt, not the
	document already created earlier in the transaction) and update the
	existing row to point at the new document instead of raising. The loser
	of a concurrent double-call still gets a real, usable document back --
	not a duplicate-key error -- even though which of the two documents ends
	up "the" answer for this key is whichever call updates last.
	"""
	frappe.db.savepoint(_IDEMPOTENCY_SAVEPOINT)
	try:
		frappe.get_doc(
			{
				"doctype": "AI Idempotency Record",
				"idempotency_key": key,
				"ref_doctype": doctype,
				"ref_name": name,
			}
		).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		frappe.db.rollback(save_point=_IDEMPOTENCY_SAVEPOINT)
		frappe.db.set_value("AI Idempotency Record", key, {"ref_doctype": doctype, "ref_name": name})


def _recall_idempotency(key: str, doctype: str) -> dict | None:
	row = frappe.db.get_value(
		"AI Idempotency Record", {"idempotency_key": key}, ["ref_doctype", "ref_name"], as_dict=True
	)
	if not row:
		return None
	if row.ref_doctype != doctype:
		# The key is a caller-chosen string with no doctype in it, so nothing
		# else ties it to *this* creation. Silently returning the other
		# doctype's document would report success while creating nothing --
		# the caller would have no signal that its Sales Invoice never
		# happened.
		raise ToolError(
			_(
				"Idempotency key {0} was already used to create a {1}, not a {2}. Use a new key "
				"for this creation."
			).format(key, row.ref_doctype, doctype)
		)
	if not frappe.db.exists(row.ref_doctype, row.ref_name):
		return None
	return {"doctype": row.ref_doctype, "name": row.ref_name}


# frappe.model.default_fields covers docstatus (plus name/owner/creation/...);
# parent/parenttype/parentfield/idx are the child-table equivalents (idx is
# already in default_fields, repeated here per the review for clarity). All
# of these are Frappe-managed bookkeeping fields, never legitimate business
# data for an agent to set directly.
FORBIDDEN_DATA_KEYS: frozenset[str] = frozenset(default_fields) | {
	"parent",
	"parenttype",
	"parentfield",
	"idx",
}


def _assert_no_forbidden_keys(data: dict) -> None:
	"""Reject (never silently strip) any caller key that is Frappe-managed
	bookkeeping rather than business data.

	This is the fix for the critical submit-bypass: `data={"docstatus": 1}`
	on a draft is a 0->1 transition that `frappe.get_doc()`/`Document.save()`
	treats as a real submit (`_action = "submit"`, `on_submit()` runs -- GL
	and stock postings) even though it never went through `submit_document`,
	the only tool the external platform's approval policy gates by name.
	Rejecting outright, rather than stripping `docstatus` and proceeding,
	gives the calling agent an actionable signal instead of hiding what it
	tried to do.
	"""
	offending = sorted(set(data) & FORBIDDEN_DATA_KEYS)
	if offending:
		raise ToolError(
			_(
				"These fields cannot be set through this tool: {0}. In particular, docstatus "
				"cannot be set here -- submitting a document only ever happens through "
				"submit_document, which the approval policy gates by name."
			).format(", ".join(offending))
		)


_VALUE_CAP_SAVEPOINT = "ai_cap"


_VALUE_CAP_FIELDS = (
	"grand_total",
	"base_grand_total",
	"total",
	"paid_amount",
	"base_paid_amount",
	"total_debit",
	"total_credit",
	"total_amount",
)


def _assert_value_cap(doc) -> None:
	"""Enforce `max_document_value` against whichever monetary field on this
	doctype is highest, not just the first of a fixed shortlist that happens
	to be populated.

	`grand_total`/`base_grand_total`/`total` alone miss doctypes that settle
	through a different field -- Payment Entry (`paid_amount`/
	`base_paid_amount`), Journal Entry (`total_debit`/`total_credit`/
	`total_amount`), Stock Entry (`total_amount`) -- for which the cap was
	previously a complete no-op. Call this only after the document has been
	settled (inserted/saved/submitted): those totals are computed by
	`AccountsController.validate()`, which runs *during* that call, not
	before it -- calling this on a freshly-built, pre-insert Document sees
	every one of these fields still at its unset default.
	"""
	label = f"{doc.doctype} {doc.name or ''}".strip()
	highest = None  # (numeric_value, raw_value)
	for fieldname in _VALUE_CAP_FIELDS:
		raw = doc.get(fieldname)
		if not raw:
			continue
		try:
			numeric = float(raw)
		except (TypeError, ValueError):
			# Non-numeric junk in a currency field is itself worth refusing;
			# let assert_value_within_cap produce its standard error for it
			# rather than silently skipping this field.
			assert_value_within_cap(raw, label)
			return
		if highest is None or numeric > highest[0]:
			highest = (numeric, raw)
	if highest is not None:
		assert_value_within_cap(highest[1], label)


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
	scoping.assert_write_allowed_doctype(doctype)
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
	_assert_no_forbidden_keys(data or {})

	existing = _recall_idempotency(idempotency_key, doctype)
	if existing:
		return {**existing, "reused": True}

	if not frappe.db.exists("DocType", doctype):
		raise ToolError(_("No such doctype: {0}").format(doctype))
	if not frappe.has_permission(doctype, "create"):
		# Same message as the nonexistent-doctype branch above, deliberately
		# -- see search_documents' identical pattern above. Checked before
		# frappe.get_doc(payload).insert() below gets a chance to raise its
		# own distinguishable frappe.PermissionError for an existing-but-
		# uncreatable doctype.
		raise ToolError(_("No such doctype: {0}").format(doctype))

	payload = dict(data or {})
	payload["doctype"] = doctype
	if scoping.has_company_field(doctype):
		if payload.get("company"):
			scoping.assert_company_allowed(payload["company"])
		else:
			payload["company"] = scoping.default_company()

	doc = frappe.get_doc(payload)
	# The value cap must be checked against the *settled* document:
	# grand_total/total/paid_amount/... are computed by
	# AccountsController.validate(), which insert() runs, not something
	# already present on the pre-insert in-memory Document. The savepoint
	# guarantees an over-cap document is never left behind if the cap check
	# fails after insert() has already written it.
	frappe.db.savepoint(_VALUE_CAP_SAVEPOINT)
	try:
		doc.insert()
		_assert_value_cap(doc)
	except ToolError:
		frappe.db.rollback(save_point=_VALUE_CAP_SAVEPOINT)
		raise
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
	_assert_no_forbidden_keys(data or {})

	for key, value in (data or {}).items():
		if key == "company":
			scoping.assert_company_allowed(value)
		doc.set(key, value)

	# Same settled-document requirement as create_document: a pre-save() doc
	# still carries its *previous* total, not one reflecting this update's
	# line-item changes.
	frappe.db.savepoint(_VALUE_CAP_SAVEPOINT)
	try:
		doc.save()
		_assert_value_cap(doc)
	except ToolError:
		frappe.db.rollback(save_point=_VALUE_CAP_SAVEPOINT)
		raise
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
	# doc is freshly loaded from the DB, so its stored totals already reflect
	# the last save -- but submit()'s own validate() pass can still move
	# valuation-derived fields (e.g. FIFO/landed cost), so re-check after,
	# under the same savepoint-and-rollback guarantee as create/update: this
	# is the point where money actually moves (GL/stock postings), so it is
	# the last place an over-cap document may slip through uncaught.
	frappe.db.savepoint(_VALUE_CAP_SAVEPOINT)
	try:
		doc.submit()
		_assert_value_cap(doc)
	except ToolError:
		frappe.db.rollback(save_point=_VALUE_CAP_SAVEPOINT)
		raise
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
