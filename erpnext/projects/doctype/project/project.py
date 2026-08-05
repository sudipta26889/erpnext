# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext.projects.project_financials import compute_financials
from erpnext.projects.taskpilot_client import TaskPilotNotFound, get_client, is_enabled
from erpnext.projects.taskpilot_mapping import make_identifier, project_to_payload, tp_to_project


class Project(Document):
	def autoname(self):
		self.name = make_identifier(self.project_name)

	def load_from_db(self):
		if not is_enabled():
			raise frappe.DoesNotExistError(
				_("Project {0} is not available (TaskPilot integration disabled)").format(self.name)
			)
		client = get_client()
		try:
			tp_project = client.get_project(self.name)
		except TaskPilotNotFound:
			raise frappe.DoesNotExistError(_("Project {0} not found").format(self.name))
		d = tp_to_project(tp_project)
		d.update(compute_financials(self.name))
		d["percent_complete"] = compute_percent_complete(self.name, client=client)
		super(Document, self).__init__(d)

	def db_insert(self, *args, **kwargs):
		client = get_client()
		payload = project_to_payload(self)
		if not payload.get("identifier"):
			payload["identifier"] = make_identifier(self.project_name)
		created = client.create_project(payload)
		self.name = created["identifier"]

	def db_update(self, *args, **kwargs):
		client = get_client()
		client.update_project(self.name, {"name": self.project_name, "description": self.notes or ""})
		before = self.get_doc_before_save()
		if self.status in ("Completed", "Cancelled") and (not before or before.status == "Open"):
			client.archive_project(self.name)

	def delete(self, *args, **kwargs):
		self.check_permission("delete")
		get_client().archive_project(self.name)

	@staticmethod
	def get_list(args):
		# Check for aggregate COUNT fields (e.g., {"COUNT": "*", "as": "count"})
		fields = args.get("fields") or []
		aggregate_spec = None
		for field in fields:
			if isinstance(field, dict):
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
		return rows[start : start + length]

	@staticmethod
	def get_count(args):
		if not is_enabled():
			return 0
		return len(_matching_rows(args))

	@staticmethod
	def get_stats(args):
		return {}


def compute_percent_complete(project: str, client=None) -> float:
	client = client or get_client()
	items = client.list_work_items(project)
	if not items:
		return 0.0
	states = {s["id"]: s for s in client.states(project)}
	done = sum(
		1 for wi in items if states.get(wi.get("state"), {}).get("group") in ("completed", "cancelled")
	)
	return round(done / len(items) * 100, 2)


def _matching_rows(args) -> list:
	client = get_client()
	rows = [tp_to_project(p) for p in client.list_projects()]
	txt = _like_value(args, ("name", "project_name"))
	if txt:
		rows = [
			r for r in rows if txt.lower() in (r.project_name or "").lower() or txt.lower() in r.name.lower()
		]
	statuses = _status_values(args)
	if statuses:
		rows = [r for r in rows if r.status in statuses]
	return rows


def _normalized_filters(args):
	"""Yield (fieldname, operator, value) from frappe's list/dict filter shapes.

	List rows come as either [fieldname, operator, value] or
	[doctype, fieldname, operator, value]; anything shorter or longer is
	not a recognized shape and is skipped rather than guessed at.
	"""
	filters = args.get("filters") or []
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


def _like_value(args, fieldnames: tuple) -> str | None:
	for fieldname, operator, value in _normalized_filters(args):
		if fieldname in fieldnames and operator in ("like", "="):
			return str(value).strip("%")
	return None


def _status_values(args) -> list | None:
	for fieldname, operator, value in _normalized_filters(args):
		if fieldname == "status":
			if operator == "in":
				return list(value)
			elif operator == "=":
				return [value]
			else:
				# ponytail: operators like !=, not in, etc. unsupported; return None (no filter)
				return None
	return None


@frappe.whitelist()
def update_costing_and_billing(project: str) -> dict:
	"""Kept for the existing form button; totals are computed on read now."""
	return compute_financials(project)


@frappe.whitelist()
def get_cost_center_name(project: str) -> str | None:
	"""Per-project cost centers are retired; return the configured fallback."""
	return frappe.get_cached_doc("TaskPilot Settings").default_cost_center
