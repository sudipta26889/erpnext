# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe

from erpnext.projects.taskpilot_client import get_client
from erpnext.projects.taskpilot_mapping import (
	STATUS_TO_STATE,
	make_identifier,
	project_to_payload,
	task_to_work_item_payload,
)


def execute():
	if not frappe.db.table_exists("Project") and not frappe.db.table_exists("Task"):
		return  # already migrated (post-model-sync: Project/Task are virtual, this is the marker)

	if _drop_empty_legacy_tables():
		return

	settings = frappe.get_doc("TaskPilot Settings")
	if not settings.enabled:
		frappe.throw("Configure and enable TaskPilot Settings before running this migration.")

	client = get_client()
	projects = client.list_projects()
	existing = {p.get("external_id"): p["identifier"] for p in projects if p.get("external_id")}
	taken = {p["identifier"] for p in projects if not p.get("external_id")}

	project_map = _push_projects(client, existing, taken)
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

	Each table is checked and dropped independently: a site can legitimately have only one of
	the two tables left (e.g. a prior run failed partway between the two final DROPs), and a
	`select count(*)` against a table that doesn't exist raises rather than returning 0.
	"""
	project_exists = frappe.db.table_exists("Project")
	task_exists = frappe.db.table_exists("Task")
	project_count = frappe.db.sql("select count(*) from `tabProject`")[0][0] if project_exists else 0
	task_count = frappe.db.sql("select count(*) from `tabTask`")[0][0] if task_exists else 0
	if project_count or task_count:
		return False
	if project_exists:
		frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabProject`")
	if task_exists:
		frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabTask`")
	return True


def _push_projects(client, existing, taken) -> dict:
	rows = frappe.db.sql("select name, project_name, notes, status from `tabProject`", as_dict=True)
	_check_identifier_collisions(rows, existing, taken)

	project_map = {}
	for row in rows:
		if row.name in existing:  # idempotent re-run
			project_map[row.name] = existing[row.name]
			continue
		payload = project_to_payload(
			frappe._dict(project_name=row.project_name, notes=row.notes, external_id=row.name)
		)
		created = client.create_project(payload)
		project_map[row.name] = created["identifier"]
		if row.status in ("Completed", "Cancelled"):
			client.archive_project(created["identifier"])
	return project_map


def _check_identifier_collisions(rows, existing, taken):
	"""Two legacy projects whose names collapse to the same TaskPilot identifier (make_identifier
	strips non-alphanumerics and truncates to 10 chars) would silently overwrite one another in
	TaskPilot - abort the whole push before anything is created so the user can rename first.
	Rows already migrated (their docname is in `existing`) are not being pushed, so they can't
	collide with anything and are excluded from the check. Also fail if a computed identifier is
	already taken by a non-migrated TaskPilot project.
	"""
	by_identifier = {}
	workspace_collisions = []
	for row in rows:
		if row.name in existing:
			continue
		identifier = make_identifier(row.project_name)
		by_identifier.setdefault(identifier, []).append(row.name)
		if identifier in taken:
			workspace_collisions.append((row.project_name, identifier))

	if workspace_collisions:
		# Cap message to 20 collisions + count of remainder
		shown = workspace_collisions[:20]
		remainder = len(workspace_collisions) - 20
		details = "; ".join(f"{name} -> '{identifier}'" for name, identifier in shown)
		if remainder > 0:
			details += f"; and {remainder} more"
		frappe.throw(
			f"Projects would collide with non-migrated TaskPilot projects - rename before migrating: {details}"
		)

	collisions = {identifier: names for identifier, names in by_identifier.items() if len(names) > 1}
	if collisions:
		# Cap message to 20 collisions + count of remainder
		collision_items = list(collisions.items())
		shown = collision_items[:20]
		remainder = len(collision_items) - 20
		details = "; ".join(f"{identifier}: {', '.join(names)}" for identifier, names in shown)
		if remainder > 0:
			details += f"; and {remainder} more identifier(s)"
		frappe.throw(
			f"Multiple projects share the same TaskPilot identifier - rename before migrating: {details}"
		)


def _push_tasks(client, project_map) -> dict:
	task_map, parent_pending = {}, []
	rows = frappe.db.sql(
		"""select name, subject, description, status, priority, exp_start_date, exp_end_date,
		parent_task, project from `tabTask` where ifnull(is_template, 0) = 0 order by lft""",
		as_dict=True,
	)
	_check_orphan_tasks(rows, project_map)

	by_project = {}
	for row in rows:
		by_project.setdefault(project_map[row.project], []).append(row)

	# Per project (not per row): the existing-work-item map for idempotent re-runs, and the set of
	# TaskPilot states actually needed for the rows that still have to be created.
	existing_by_identifier, states_by_identifier = {}, {}
	for identifier, project_rows in by_project.items():
		existing = {
			wi["external_id"]: f"{identifier}-{wi['sequence_id']}"
			for wi in client.list_work_items(identifier)
			if wi.get("external_id")
		}
		existing_by_identifier[identifier] = existing
		needed_statuses = {
			row.status if row.status in STATUS_TO_STATE else "Open"
			for row in project_rows
			if row.name not in existing
		}
		states_by_identifier[identifier] = {
			status: client.ensure_state(identifier, *STATUS_TO_STATE[status])["id"]
			for status in needed_statuses
		}

	# ponytail: TaskPilotClient.invalidate_cache() flushes the *entire* per-workspace cache
	# namespace on every non-GET request (see TaskPilotClient.request), so each create_work_item
	# call below leaves the next GET (states/work-items lookups) cold - several requests per task
	# created, not one. TaskPilotClient.request retries a 429 exactly once (capped 30s wait), which
	# may not survive that multiplied request volume on a very large migration. Ceiling: fine at
	# survey/typical-site scale. Upgrade path if a real site needs more: scope cache invalidation to
	# the affected path instead of the whole namespace, or warm/batch the state and work-item lookups
	# once per project up front instead of relying on the (now-thrashed) GET cache mid-loop.
	for row in rows:
		identifier = project_map[row.project]
		existing = existing_by_identifier[identifier]
		if row.name in existing:  # idempotent re-run: already pushed under this external_id
			task_map[row.name] = existing[row.name]
			if row.parent_task:
				parent_pending.append((task_map[row.name], row.parent_task))
			continue
		status = row.status if row.status in STATUS_TO_STATE else "Open"
		state_id = states_by_identifier[identifier][status]
		payload = task_to_work_item_payload(row, state_id=state_id)
		payload["external_id"] = row.name
		created = client.create_work_item(identifier, payload)
		task_map[row.name] = f"{identifier}-{created['sequence_id']}"
		if row.parent_task:
			parent_pending.append((task_map[row.name], row.parent_task))

	for child_docname, old_parent in parent_pending:
		if old_parent in task_map:
			parent_uuid = client.get_work_item(task_map[old_parent])["id"]
			client.update_work_item(child_docname, {"parent": parent_uuid})
	return task_map


def _check_orphan_tasks(rows, project_map):
	"""A task whose project was never migrated (dangling FK to a Project row that no longer
	exists) has no TaskPilot home. Abort before any push - and well before the irreversible
	DROP TABLE at the end of execute() - rather than silently dropping it on the floor."""
	orphans = [(row.name, row.project) for row in rows if row.project not in project_map]
	if orphans:
		# Cap message to 20 orphans + count of remainder
		shown = orphans[:20]
		remainder = len(orphans) - 20
		listing = ", ".join(f"{name} (project: {project})" for name, project in shown)
		if remainder > 0:
			listing += f", and {remainder} more"
		frappe.throw(
			f"{len(orphans)} task(s) reference an unmigrated project - fix before migrating: {listing}"
		)


def _get_dynamic_ref_tables() -> list[tuple[str, str, str]]:
	"""Derive dynamic reference table mappings from DocField metadata, plus hardcoded Data-column
	stragglers that use Text/Data fields instead of Dynamic Link fieldtype.

	Returns: list of (doctype, type_col, name_col) tuples for polymorphic references.
	"""
	# Dynamic Link fields detected via metadata
	dynamic_links = frappe.get_all(
		"DocField",
		filters={"fieldtype": "Dynamic Link"},
		fields=["parent as doctype", "fieldname", "options as type_col"],
	)
	tables = [(row.doctype, row.type_col, row.fieldname) for row in dynamic_links]

	# Data-column stragglers that use Text/Data fields instead of Dynamic Link fieldtype
	DATA_COLUMN_STRAGGLERS = [
		("File", "attached_to_doctype", "attached_to_name"),
		("Version", "ref_doctype", "docname"),
		("Notification Log", "document_type", "document_name"),
	]
	tables.extend(DATA_COLUMN_STRAGGLERS)

	return tables


def _link_columns(target_doctype: str) -> list[tuple[str, str]]:
	rows = frappe.get_all(
		"DocField",
		filters={"fieldtype": "Link", "options": target_doctype},
		fields=["parent as doctype", "fieldname"],
	)
	rows += frappe.get_all(
		"Custom Field",
		filters={"fieldtype": "Link", "options": target_doctype},
		fields=["dt as doctype", "fieldname"],
	)
	return [(r.doctype, r.fieldname) for r in rows if r.doctype not in (target_doctype, "Project", "Task")]


def _remap_links(project_map, task_map):
	for doctype, column in _link_columns("Project"):
		_remap(doctype, column, project_map)
	for doctype, column in _link_columns("Task"):
		_remap(doctype, column, task_map)
	for table_doctype, type_col, name_col in _get_dynamic_ref_tables():
		_remap_dynamic(table_doctype, project_map, type_col, name_col, "Project")
		_remap_dynamic(table_doctype, task_map, type_col, name_col, "Task")


def _remap(doctype, column, mapping):
	# ponytail: _remap generates per-(table x column x project) UPDATEs across ~43 Link fields;
	# _remap_dynamic adds per-(table x type_col x type_value) UPDATEs across ~112 Dynamic Link field
	# entries + 3 Data-column stragglers, each x 2 type_values (Project/Task), mostly filtered to
	# zero rows by the type predicate (GL Entry/Payment Entry refs). Upgrade path if volume demands:
	# single CASE-expression UPDATE per column or temp mapping table join.
	if not frappe.db.table_exists(doctype):
		return
	table = frappe.qb.DocType(doctype)
	for old, new in mapping.items():
		frappe.qb.update(table).set(table[column], new).where(table[column] == old).run()
	frappe.db.commit()


def _remap_dynamic(doctype_name, mapping, type_col, name_col, type_value):
	"""Remap docnames stored in generic (type, name) reference columns - e.g. `tabToDo.reference_name`,
	`tabComment.reference_name`, etc. - which aren't Link fields, so `_link_columns` can't find them and
	the docname would otherwise orphan silently once the old Project/Task docname stops resolving. Caller
	(see _get_dynamic_ref_tables) provides the table, type_col, name_col mapping.
	"""
	if not mapping or not frappe.db.table_exists(doctype_name):
		return
	table = frappe.qb.DocType(doctype_name)
	for old, new in mapping.items():
		frappe.qb.update(table).set(table[name_col], new).where(
			(table[type_col] == type_value) & (table[name_col] == old)
		).run()
	frappe.db.commit()
