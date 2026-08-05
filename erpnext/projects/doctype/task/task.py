# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc

from erpnext.projects.taskpilot_client import TaskPilotNotFound, get_client, is_enabled
from erpnext.projects.taskpilot_mapping import (
	STATUS_TO_STATE,
	task_to_work_item_payload,
	work_item_to_task,
)


class Task(Document):
	def load_from_db(self):
		if not is_enabled():
			raise frappe.DoesNotExistError(
				_("Task {0} is not available (TaskPilot integration disabled)").format(self.name)
			)
		client = get_client()
		project_identifier = self.name.rsplit("-", 1)[0]
		try:
			wi = client.get_work_item(self.name)
		except TaskPilotNotFound:
			raise frappe.DoesNotExistError(_("Task {0} not found").format(self.name))
		states = {s["id"]: s for s in client.states(project_identifier)}
		parent_docname = (
			client.work_item_identifier(project_identifier, wi["parent"]) if wi.get("parent") else None
		)
		emails = _assignee_emails(client, wi)
		super(Document, self).__init__(
			work_item_to_task(wi, states, project_identifier, parent_docname, emails)
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
				result = [row + [0] for row in result]
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


def _normalized_filters(filters):
	"""Yield (fieldname, operator, value) from frappe's list/dict filter shapes.

	List rows come as either [fieldname, operator, value] or
	[doctype, fieldname, operator, value]; anything shorter or longer is
	not a recognized shape and is skipped rather than guessed at.
	"""
	filters = filters or []
	if isinstance(filters, dict):
		for k, v in filters.items():
			if isinstance(v, (list, tuple)) and len(v) == 2:
				yield (k, v[0], v[1])
			else:
				yield (k, "=", v)
		return
	for f in filters:
		if len(f) == 3:
			yield (f[0], f[1], f[2])
		elif len(f) == 4:
			yield (f[1], f[2], f[3])


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
