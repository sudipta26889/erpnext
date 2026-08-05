# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils import getdate

from erpnext.projects.taskpilot_client import (
	TaskPilotError,
	TaskPilotNotFound,
	get_client,
	is_enabled,
	is_lenient,
)
from erpnext.projects.taskpilot_mapping import (
	STATUS_TO_STATE,
	task_to_work_item_payload,
	work_item_to_task,
)


class Task(Document):
	def load_from_db(self):
		project_identifier = self.name.rsplit("-", 1)[0] if "-" in self.name else None
		if not is_enabled():
			if is_lenient():
				return self._load_stub(project_identifier)
			raise frappe.DoesNotExistError(
				_("Task {0} is not available (TaskPilot integration disabled)").format(self.name)
			)
		client = get_client()
		try:
			wi = client.get_work_item(self.name)
		except TaskPilotNotFound:
			raise frappe.DoesNotExistError(_("Task {0} not found").format(self.name))
		except TaskPilotError:
			if is_lenient():
				return self._load_stub(project_identifier)
			raise
		states = {s["id"]: s for s in client.states(project_identifier)}
		parent_docname = (
			client.work_item_identifier(project_identifier, wi["parent"]) if wi.get("parent") else None
		)
		emails = _assignee_emails(client, wi)
		super(Document, self).__init__(
			work_item_to_task(wi, states, project_identifier, parent_docname, emails)
		)

	def _load_stub(self, project_identifier):
		# spec §5: transaction saves that only validate a task link must warn-and-proceed (not
		# hard fail) when TaskPilot is disabled/unreachable and lenient_link_validation is on.
		# Populate just enough of the doc for the link validator / a read-only render.
		super(Document, self).__init__(
			frappe._dict(
				doctype="Task",
				name=self.name,
				subject=self.name,
				project=project_identifier,
				status="Open",
				docstatus=0,
				idx=0,
			)
		)

	def validate(self):
		self.validate_from_to_dates("exp_start_date", "exp_end_date")

	def db_insert(self, *args, **kwargs):
		if not self.project:
			frappe.throw(_("Task needs a Project (TaskPilot work items live inside a project)."))
		client = get_client()
		created = client.create_work_item(self.project, self._payload(client))
		self.name = f"{self.project}-{created['sequence_id']}"

	def db_update(self, *args, **kwargs):
		client = get_client()
		client.update_work_item(self.name, self._payload(client))

	def delete(self, *args, **kwargs):
		self.check_permission("delete")
		# TaskPilot has no hard delete: move to the cancelled-group state instead
		client = get_client()
		project_identifier = self.name.rsplit("-", 1)[0]
		state = client.ensure_state(project_identifier, "Cancelled", "cancelled")
		client.update_work_item(self.name, {"state": state["id"]})

	def _payload(self, client) -> dict:
		project_identifier = self.project or self.name.rsplit("-", 1)[0]
		state_id = None
		if self.status and self.status not in ("Overdue",):
			name, group = STATUS_TO_STATE.get(self.status, ("Todo", "unstarted"))
			state_id = client.ensure_state(project_identifier, name, group)["id"]
		parent_uuid = client.get_work_item(self.parent_task)["id"] if self.parent_task else None
		payload = task_to_work_item_payload(self, state_id=state_id, parent_uuid=parent_uuid)
		# task_to_work_item_payload only sets "parent" when parent_uuid is truthy (PATCH semantics
		# would then silently keep the old parent); set it unconditionally so None unparents.
		payload["parent"] = parent_uuid
		return payload

	@staticmethod
	def get_list(args):
		# Check for aggregate COUNT fields (e.g., {"COUNT": "*", "as": "count"})
		fields = args.get("fields") or []
		aggregate_spec = None
		for field in fields:
			if isinstance(field, dict) and "COUNT" in field:
				aggregate_spec = field
				break

		if aggregate_spec:
			alias = aggregate_spec.get("as") or "result"
			if not is_enabled():
				return [frappe._dict({alias: 0})]
			count = len(_matching_rows(args))
			return [frappe._dict({alias: count})]

		if not is_enabled():
			return []
		rows = _matching_rows(args)
		start = int(args.get("start") or args.get("limit_start") or 0)
		if _has_date_range_filter(args):
			# frappe.desk.calendar.get_events / the Gantt view filter Task by exp_start_date/
			# exp_end_date range but never pass an explicit page_length - and frappe's virtual
			# doctype dispatch (qb_query.py: `page_length or limit or limit_page_length or 20`)
			# always fills in 20 before this method ever sees args, so "caller passed none" is
			# indistinguishable here from "caller wants exactly 20". Honor the date-range intent
			# instead: a calendar/Gantt window wants every matching row, not an arbitrary first 20.
			length = 10**6
		else:
			length = int(args.get("page_length") or args.get("limit_page_length") or 20)
		rows = rows[start : start + length]
		if args.get("as_list"):
			# frappe.desk.search calls get_list(as_list=True) for link-field dropdowns
			# and indexes the result positionally. Detect whether the caller requested
			# the relevance expression; if so, append a trailing 0 (frappe.desk.search
			# strips the last column: search.py).
			wants_relevance = any(isinstance(f, dict) and f.get("as") == "_relevance" for f in fields)
			result = [[r.name, r.subject] for r in rows]
			if wants_relevance:
				result = [[*row, 0] for row in result]
			return result
		return rows

	@staticmethod
	def get_count(args):
		if not is_enabled():
			return 0
		return len(_matching_rows(args))

	@staticmethod
	def get_stats(args):
		return {}


def _assignee_emails(client, wi) -> list[str]:
	if not wi.get("assignees"):
		return []
	by_id = {m["id"]: m.get("email") for m in client.members()}
	return [e for e in (by_id.get(a) for a in wi["assignees"]) if e]


_DATE_FIELDS = ("exp_start_date", "exp_end_date")
# Plain lambdas rather than the stdlib `operator` module: this file already uses `operator` as
# the loop-variable name for a filter tuple's comparator string everywhere else (see
# _normalized_filters below), and shadowing that convention module-wide isn't worth avoiding
# four one-line lambdas.
_DATE_OPERATORS = {
	">=": lambda a, b: a >= b,
	"<=": lambda a, b: a <= b,
	">": lambda a, b: a > b,
	"<": lambda a, b: a < b,
}


def _has_date_range_filter(args) -> bool:
	return any(
		fieldname in _DATE_FIELDS and op in _DATE_OPERATORS
		for fieldname, op, _value in _normalized_filters(args.get("filters"))
	)


def _apply_date_filters(args, rows: list) -> list:
	"""Honor >=/<=/>/< filters on exp_start_date/exp_end_date - the shape
	frappe.desk.calendar.get_events sends (via get_event_conditions_qb) for Gantt/Calendar
	views. Rows with no value for a date-filtered field are excluded rather than treated as
	a match, since getdate(None) has no meaningful ordering against a real date.
	"""
	for fieldname, op, value in _normalized_filters(args.get("filters")):
		if fieldname not in _DATE_FIELDS or op not in _DATE_OPERATORS:
			continue
		wanted = getdate(value)
		cmp = _DATE_OPERATORS[op]
		rows = [r for r in rows if r.get(fieldname) and cmp(getdate(r[fieldname]), wanted)]
	return rows


def _matching_rows(args) -> list:
	client = get_client()
	projects = _values(args, "project")
	identifiers = projects if projects else [p["identifier"] for p in client.list_projects()]

	rows = []
	for identifier in identifiers:
		states = {s["id"]: s for s in client.states(identifier)}
		items = client.list_work_items(identifier)
		uuid_to_docname = {wi["id"]: f"{identifier}-{wi['sequence_id']}" for wi in items}
		for wi in items:
			parent_docname = uuid_to_docname.get(wi["parent"]) if wi.get("parent") else None
			rows.append(work_item_to_task(wi, states, identifier, parent_docname))

	rows = _apply_date_filters(args, rows)

	statuses = _values(args, "status")
	if statuses:
		rows = [r for r in rows if r.status in statuses]

	parents = _values(args, "parent_task")
	if parents is not None:
		wanted = {(p or "") for p in parents}
		rows = [r for r in rows if (r.parent_task or "") in wanted]

	# frappe.desk.search's link-field dropdown (e.g. Task.parent_task) sends the typed
	# text via or_filters, not filters; fall back to it the same way _like_value reads filters.
	subject = _like_value(args.get("filters"), ("subject", "name")) or _like_value(
		args.get("or_filters"), ("subject", "name")
	)
	if subject:
		needle = subject.lower()
		rows = [r for r in rows if needle in (r.subject or "").lower() or needle in r.name.lower()]

	return rows


def _unwrap_fieldname(fieldname):
	"""Return the plain string fieldname for a filter row's fieldname slot.

	frappe.desk.calendar.get_events (frappe/desk/calendar.py get_events, via
	get_event_conditions_qb -> frappe.get_list) sends the calendar/Gantt date-range filter
	with the fieldname slot holding a PyPika term, not a string - e.g.
	`functions.IfNull(dt["exp_start_date"], ValueWrapper("0001-01-01 00:00:00"))`. PyPika's
	Term.__eq__ (pypika.terms, pypika==0.48.9) returns a BasicCriterion, an always-truthy
	object, rather than True/False - so `fieldname in _DATE_FIELDS` and any `fieldname ==
	"..."` check involving such a term is truthy for EVERY string, not just a real match.
	Left unhandled, every filter row looks like a match against every field name.

	Unwrap via Term.fields_() (pypika.terms.Term.fields_, inherited by Function/Field/
	Criterion), which walks the term's node tree (nodes_()) back to the Field node(s) it
	wraps. If exactly one Field is found, its .name is the real fieldname. Anything else
	(zero fields, more than one - ambiguous, or not a PyPika term at all) is not a shape we
	can safely resolve, so the row is skipped rather than guessed at.
	"""
	if isinstance(fieldname, str):
		return fieldname
	fields_ = getattr(fieldname, "fields_", None)
	if not callable(fields_):
		return None
	try:
		found = fields_()
	except Exception:
		return None
	if len(found) != 1:
		return None
	return next(iter(found)).name


def _normalized_filters(filters):
	"""Yield (fieldname, operator, value) from frappe's list/dict filter shapes.

	List rows come as either [fieldname, operator, value] or
	[doctype, fieldname, operator, value]; anything shorter or longer is
	not a recognized shape and is skipped rather than guessed at. fieldname is normalized
	to a plain string via _unwrap_fieldname (see there for why it isn't always one already);
	rows whose fieldname can't be resolved to a single string are skipped.
	"""
	filters = filters or []
	if isinstance(filters, dict):
		rows = (
			(k, v[0], v[1]) if isinstance(v, (list, tuple)) and len(v) == 2 else (k, "=", v)
			for k, v in filters.items()
		)
	else:
		rows = (
			(f[0], f[1], f[2]) if len(f) == 3 else (f[1], f[2], f[3]) for f in filters if len(f) in (3, 4)
		)
	for fieldname, operator, value in rows:
		fieldname = _unwrap_fieldname(fieldname)
		if fieldname is None:
			continue
		yield (fieldname, operator, value)


def _values(args, fieldname: str) -> list | None:
	"""Wanted values for `fieldname` from a = or in filter; None means no filter."""
	for fname, operator, value in _normalized_filters(args.get("filters")):
		if fname != fieldname:
			continue
		if operator == "=":
			return [value]
		elif operator == "in":
			return list(value)
		else:
			# ponytail: operators like !=, not in, etc. unsupported; return None (no filter)
			return None
	return None


def _like_value(filters, fieldnames: tuple) -> str | None:
	for fieldname, operator, value in _normalized_filters(filters):
		if fieldname in fieldnames and operator in ("like", "="):
			return str(value).strip("%")
	return None


@frappe.whitelist()
def get_children(
	doctype: str,
	parent: str | None = None,
	task: str | None = None,
	project: str | None = None,
	is_root: bool = False,
) -> list[dict]:
	parent_task = task or (parent if parent and not is_root and parent != "All Tasks" else None)
	args = {"filters": [["Task", "parent_task", "=", parent_task or ""]], "page_length": 500}
	if project:
		args["filters"].append(["Task", "project", "=", project])
	rows = Task.get_list(args)
	children = frappe._dict()
	for r in Task.get_list(
		{"filters": [["Task", "project", "=", project]] if project else [], "page_length": 10**6}
	):
		if r.parent_task:
			children[r.parent_task] = True
	return [
		{"value": r.name, "title": r.subject, "expandable": 1 if children.get(r.name) else 0} for r in rows
	]


@frappe.whitelist(methods=["POST"])
def add_node():
	from frappe.desk.treeview import make_tree_args

	args = frappe.form_dict
	args.update({"name_field": "subject"})
	args = make_tree_args(**args)
	if args.parent_task in ("All Tasks", args.project):
		args.parent_task = None
	frappe.get_doc(args).insert()


@frappe.whitelist(methods=["POST"])
def add_multiple_tasks(data: str | list, parent: str):
	data = frappe.parse_json(data)
	parent_doc = frappe.get_doc("Task", parent) if parent and parent != "All Tasks" else None
	for d in data:
		if not d.get("subject"):
			continue
		frappe.get_doc(
			{
				"doctype": "Task",
				"subject": d["subject"],
				"parent_task": parent_doc.name if parent_doc else None,
				"project": parent_doc.project if parent_doc else None,
			}
		).insert()


@frappe.whitelist(methods=["POST"])
def set_multiple_status(names: str | list, status: str):
	names = frappe.parse_json(names)
	for name in names:
		task = frappe.get_doc("Task", name)
		task.status = status
		task.save()


@frappe.whitelist()
def make_timesheet(
	source_name: str, target_doc: str | dict | Document | None = None, ignore_permissions: bool = False
):
	def set_missing_values(source, target):
		target.parent_project = source.project
		target.append("time_logs", {"project": source.project, "task": source.name})

	return get_mapped_doc(
		"Task",
		source_name,
		{"Task": {"doctype": "Timesheet"}},
		target_doc,
		postprocess=set_missing_values,
		ignore_permissions=ignore_permissions,
	)
