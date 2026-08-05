# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe

from erpnext.projects.taskpilot_client import get_client
from erpnext.projects.taskpilot_mapping import STATUS_TO_STATE, make_identifier

# Link columns that store a Project name (from the integration survey; child tables included)
PROJECT_LINK_COLUMNS = [
	("GL Entry", "project"),
	("Payment Ledger Entry", "project"),
	("Account Closing Balance", "project"),
	("Sales Invoice", "project"),
	("Sales Invoice Item", "project"),
	("Purchase Invoice", "project"),
	("Purchase Invoice Item", "project"),
	("POS Invoice", "project"),
	("POS Invoice Item", "project"),
	("Journal Entry Account", "project"),
	("Payment Entry", "project"),
	("Payment Request", "project"),
	("Sales Order", "project"),
	("Sales Order Item", "project"),
	("Delivery Note", "project"),
	("Delivery Note Item", "project"),
	("Purchase Order", "project"),
	("Purchase Order Item", "project"),
	("Purchase Receipt", "project"),
	("Purchase Receipt Item", "project"),
	("Supplier Quotation", "project"),
	("Supplier Quotation Item", "project"),
	("Material Request Item", "project"),
	("Stock Entry", "project"),
	("Stock Entry Detail", "project"),
	("Stock Ledger Entry", "project"),
	("Stock Reservation Entry", "project"),
	("Work Order", "project"),
	("BOM", "project"),
	("BOM Creator", "project"),
	("Job Card", "project"),
	("Production Plan", "project"),
	("Timesheet", "parent_project"),
	("Timesheet Detail", "project"),
	("Budget", "project"),
	("Issue", "project"),
	("Subcontracting Order", "project"),
	("Subcontracting Order Item", "project"),
	("Subcontracting Receipt", "project"),
	("Subcontracting Receipt Item", "project"),
	("Asset Repair", "project"),
	("Asset Capitalization", "project"),
	("Installation Note", "project"),
	("Request for Quotation Item", "project_name"),
]
TASK_LINK_COLUMNS = [("Timesheet Detail", "task")]


def execute():
	if not frappe.db.table_exists("Project"):
		return  # already migrated (post-model-sync: Project/Task are virtual, this is the marker)

	if _drop_empty_legacy_tables():
		return

	settings = frappe.get_doc("TaskPilot Settings")
	if not settings.enabled:
		frappe.throw("Configure and enable TaskPilot Settings before running this migration.")

	client = get_client()
	existing = {p.get("external_id"): p["identifier"] for p in client.list_projects() if p.get("external_id")}

	project_map = _push_projects(client, existing)
	task_map = _push_tasks(client, project_map)
	_remap_links(project_map, task_map)

	frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabProject`")
	frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabTask`")


def _drop_empty_legacy_tables() -> bool:
	"""A site with the legacy tables present but zero rows (fresh install, or a dev site that
	never had real project/task data) has nothing to push. Drop the shells and skip the
	TaskPilot Settings requirement entirely rather than wedging `bench migrate` behind
	"configure TaskPilot first" for tables that will never populate. Returns True if it dropped
	(caller should return immediately); False means there are real rows to migrate.
	"""
	project_count = frappe.db.sql("select count(*) from `tabProject`")[0][0]
	task_count = frappe.db.sql("select count(*) from `tabTask`")[0][0]
	if project_count or task_count:
		return False
	frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabProject`")
	frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabTask`")
	return True


def _push_projects(client, existing) -> dict:
	project_map = {}
	rows = frappe.db.sql("select name, project_name, notes, status from `tabProject`", as_dict=True)
	for row in rows:
		if row.name in existing:  # idempotent re-run
			project_map[row.name] = existing[row.name]
			continue
		created = client.create_project(
			{
				"name": row.project_name or row.name,
				"identifier": make_identifier(row.project_name or row.name),
				"external_source": "erpnext",
				"external_id": row.name,
			}
		)
		project_map[row.name] = created["identifier"]
		if row.status in ("Completed", "Cancelled"):
			client.archive_project(created["identifier"])
	return project_map


def _push_tasks(client, project_map) -> dict:
	task_map, parent_pending = {}, []
	rows = frappe.db.sql(
		"""select name, subject, description, status, priority, exp_start_date, exp_end_date,
		parent_task, project from `tabTask` where ifnull(is_template, 0) = 0 order by lft""",
		as_dict=True,
	)
	for row in rows:
		identifier = project_map.get(row.project)
		if not identifier:
			continue  # task without a (migrated) project has no TaskPilot home; logged below
		status = row.status if row.status in STATUS_TO_STATE else "Open"
		name, group = STATUS_TO_STATE[status]
		state = client.ensure_state(identifier, name, group)
		created = client.create_work_item(
			identifier,
			{
				"name": row.subject,
				"description_html": row.description or "<p></p>",
				"priority": {"Low": "low", "Medium": "medium", "High": "high", "Urgent": "urgent"}.get(
					row.priority, "none"
				),
				"start_date": str(row.exp_start_date) if row.exp_start_date else None,
				"target_date": str(row.exp_end_date) if row.exp_end_date else None,
				"state": state["id"],
				"external_source": "erpnext",
				"external_id": row.name,
			},
		)
		task_map[row.name] = f"{identifier}-{created['sequence_id']}"
		if row.parent_task:
			parent_pending.append((task_map[row.name], row.parent_task))
	skipped = [r.name for r in rows if r.project not in project_map]
	if skipped:
		print(f"Skipped {len(skipped)} tasks without migrated project: {skipped[:20]}")
	# ponytail: creates are POSTs against a 300/min service-token budget; TaskPilotClient.request()
	# retries a 429 exactly once (capped 30s wait). That's fine at survey scale, but a migration
	# pushing thousands of tasks can blow through the single retry and raise TaskPilotError mid-run.
	# Ceiling: a few hundred creates per run is safe; upgrade path if a real site needs more is a
	# jittered multi-attempt backoff in TaskPilotClient.request (or a time.sleep throttle here).
	for child_docname, old_parent in parent_pending:
		if old_parent in task_map:
			parent_uuid = client.get_work_item(task_map[old_parent])["id"]
			client.update_work_item(child_docname, {"parent": parent_uuid})
	return task_map


def _remap_links(project_map, task_map):
	for doctype, column in PROJECT_LINK_COLUMNS:
		_remap(doctype, column, project_map)
	for doctype, column in TASK_LINK_COLUMNS:
		_remap(doctype, column, task_map)


def _remap(doctype, column, mapping):
	if not frappe.db.table_exists(doctype):
		return
	table = frappe.qb.DocType(doctype)
	for old, new in mapping.items():
		frappe.qb.update(table).set(table[column], new).where(table[column] == old).run()
	frappe.db.commit()
