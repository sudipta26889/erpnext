# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

"""Legacy `tabProject`/`tabTask` are, at most, present-but-empty on a virtual-backend site (schema
sync skips virtual doctypes, so the tables only linger from a pre-virtualization install). Once
the migration patch (or this test module) drops them, they're gone for good - so real-row
coverage below degrades gracefully to an in-memory/mocked-`frappe.db.sql` variant once that
happens. Test classes are named so `TestShortCircuitDropsEmptyTables` - the one that may
permanently drop the tables - sorts alphabetically last within this module and always runs
after the idempotency test that still wants the tables around.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.patches.v16_0 import migrate_projects_to_taskpilot as patch_module


def _mock_client():
	client = MagicMock()
	client.create_project.side_effect = lambda payload: dict(payload, id="proj-uuid")
	sequence_ids = iter(range(1, 1000))
	client.create_work_item.side_effect = lambda identifier, payload: dict(
		payload, sequence_id=next(sequence_ids)
	)
	client.ensure_state.return_value = {"id": "state-1"}
	client.get_work_item.return_value = {"id": "wi-uuid"}
	return client


class TestPushProjectsIdempotency(IntegrationTestCase):
	def test_idempotent_reuses_existing_external_id(self):
		"""A project whose external_id is already in TaskPilot's `existing` map is not re-created."""
		client = _mock_client()
		if frappe.db.table_exists("Project"):
			frappe.db.sql(
				"insert into `tabProject` (name, project_name, status) values (%s, %s, %s)",
				("TEST-MIGRATION-PROJ", "Legacy Proj", "Open"),
			)
			frappe.db.commit()

			def _cleanup():
				frappe.db.sql("delete from `tabProject` where name=%s", ("TEST-MIGRATION-PROJ",))
				frappe.db.commit()

			self.addCleanup(_cleanup)
			project_map = patch_module._push_projects(client, existing={"TEST-MIGRATION-PROJ": "EXISTINGID"})
		else:
			# ponytail: tables already gone (a prior run of this module, or `bench migrate`, already
			# dropped them) - same code path, fed an in-memory row instead of a real SQL select.
			row = frappe._dict(
				name="TEST-MIGRATION-PROJ", project_name="Legacy Proj", notes=None, status="Open"
			)
			with patch.object(frappe.db, "sql", return_value=[row]):
				project_map = patch_module._push_projects(
					client, existing={"TEST-MIGRATION-PROJ": "EXISTINGID"}
				)

		client.create_project.assert_not_called()
		self.assertEqual(project_map["TEST-MIGRATION-PROJ"], "EXISTINGID")

	def test_creates_new_project_and_archives_completed(self):
		"""No existing-map hit -> client.create_project() is called, and a Completed project gets archived."""
		client = _mock_client()
		row = frappe._dict(name="OLD-PROJ", project_name="Fresh Proj", notes="<p>n</p>", status="Completed")
		with patch.object(frappe.db, "sql", return_value=[row]):
			project_map = patch_module._push_projects(client, existing={})

		client.create_project.assert_called_once()
		payload = client.create_project.call_args[0][0]
		self.assertEqual(payload["external_id"], "OLD-PROJ")
		self.assertEqual(project_map["OLD-PROJ"], payload["identifier"])
		client.archive_project.assert_called_once_with(payload["identifier"])


class TestPushTasksMapping(IntegrationTestCase):
	def test_push_tasks_links_parent_and_maps_status(self):
		rows = [
			frappe._dict(
				name="OLD-PARENT",
				subject="Parent",
				description=None,
				status="Completed",
				priority="High",
				exp_start_date=None,
				exp_end_date=None,
				parent_task=None,
				project="OLDPROJ",
			),
			frappe._dict(
				name="OLD-CHILD",
				subject="Child",
				description="<p>desc</p>",
				status="Open",
				priority="Low",
				exp_start_date=None,
				exp_end_date=None,
				parent_task="OLD-PARENT",
				project="OLDPROJ",
			),
			frappe._dict(
				name="ORPHAN",
				subject="No project",
				description=None,
				status="Open",
				priority="Low",
				exp_start_date=None,
				exp_end_date=None,
				parent_task=None,
				project="UNMIGRATED",
			),
		]
		client = _mock_client()
		with patch.object(frappe.db, "sql", return_value=rows):
			task_map = patch_module._push_tasks(client, project_map={"OLDPROJ": "NEWPROJ"})

		# the orphan (project never migrated) is skipped, not pushed
		self.assertEqual(set(task_map), {"OLD-PARENT", "OLD-CHILD"})
		# the child's parent link is patched in once the parent's TaskPilot uuid is known
		client.update_work_item.assert_called_once_with(task_map["OLD-CHILD"], {"parent": "wi-uuid"})


class TestRemapLinks(IntegrationTestCase):
	"""_remap() writes to the destination doctype's own table (e.g. Issue) - never to
	tabProject/tabTask - so these tests are safe regardless of legacy-table state."""

	def test_remap_updates_seeded_link_column(self):
		frappe.db.sql(
			"insert into `tabIssue` (name, project) values (%s, %s)", ("TEST-MIGRATION-ISSUE", "OLDPROJ")
		)
		frappe.db.commit()

		def _cleanup():
			frappe.db.sql("delete from `tabIssue` where name=%s", ("TEST-MIGRATION-ISSUE",))
			frappe.db.commit()

		self.addCleanup(_cleanup)

		patch_module._remap("Issue", "project", {"OLDPROJ": "NEWPROJ"})

		self.assertEqual(frappe.db.get_value("Issue", "TEST-MIGRATION-ISSUE", "project"), "NEWPROJ")

	def test_remap_noop_for_missing_table(self):
		# doesn't throw for a doctype whose table doesn't exist at patch runtime
		patch_module._remap("Nonexistent Doctype For Migration Test", "project", {"OLD": "NEW"})


class TestShortCircuitDropsEmptyTables(IntegrationTestCase):
	def test_short_circuit_drops_empty_tables_or_already_migrated(self):
		"""Empty legacy tables are dropped WITHOUT requiring TaskPilot Settings to be enabled; an
		already-migrated site (tables absent) is a no-op. Either way, execute() must never throw
		just because settings are disabled - only non-empty tables require that."""
		frappe.db.set_single_value("TaskPilot Settings", "enabled", 0)

		if frappe.db.table_exists("Project"):
			self.assertEqual(frappe.db.sql("select count(*) from `tabProject`")[0][0], 0)
			self.assertEqual(frappe.db.sql("select count(*) from `tabTask`")[0][0], 0)
			patch_module.execute()
			self.assertFalse(frappe.db.table_exists("Project", cached=False))
			self.assertFalse(frappe.db.table_exists("Task", cached=False))
		else:
			patch_module.execute()  # already migrated: must return quietly, not throw
