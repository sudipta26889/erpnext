# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext.projects.project_financials import compute_financials
from erpnext.projects.taskpilot_client import get_client
from erpnext.projects.taskpilot_mapping import make_identifier, project_to_payload, tp_to_project


class Project(Document):
	def load_from_db(self):
		client = get_client()
		d = tp_to_project(client.get_project(self.name))
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
		get_client().archive_project(self.name)

	@staticmethod
	def get_list(args):
		client = get_client()
		rows = [tp_to_project(p) for p in client.list_projects()]
		txt = _like_value(args, ("name", "project_name"))
		if txt:
			rows = [
				r
				for r in rows
				if txt.lower() in (r.project_name or "").lower() or txt.lower() in r.name.lower()
			]
		start = int(args.get("start") or args.get("limit_start") or 0)
		length = int(args.get("page_length") or args.get("limit_page_length") or 20)
		return rows[start : start + length]

	@staticmethod
	def get_count(args):
		return len(get_client().list_projects())

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


def _like_value(args, fieldnames: tuple) -> str | None:
	filters = args.get("filters") or []
	if isinstance(filters, dict):
		filters = [[k, "=", v] for k, v in filters.items()]
	for f in filters:
		row = f if len(f) == 3 else f[1:]
		if row[0] in fieldnames and row[1] in ("like", "="):
			return str(row[2]).strip("%")
	return None


@frappe.whitelist()
def update_costing_and_billing(project: str) -> dict:
	"""Kept for the existing form button; totals are computed on read now."""
	return compute_financials(project)


@frappe.whitelist()
def get_cost_center_name(project: str) -> str | None:
	"""Per-project cost centers are retired; return the configured fallback."""
	return frappe.get_cached_doc("TaskPilot Settings").default_cost_center
