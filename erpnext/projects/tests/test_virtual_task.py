# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.projects.tests import fixtures


def _mock(mock_get_client):
	c = mock_get_client.return_value
	c.get_work_item.return_value = fixtures.WORK_ITEM
	c.states.return_value = fixtures.STATES
	c.list_projects.return_value = [fixtures.PROJECT]
	c.list_work_items.return_value = [fixtures.WORK_ITEM]
	c.members.return_value = fixtures.MEMBERS
	c.ensure_state.side_effect = lambda p, name, group: next(s for s in fixtures.STATES if s["name"] == name)
	return c


@patch("erpnext.projects.doctype.task.task.get_client")
class TestVirtualTask(IntegrationTestCase):
	def setUp(self):
		# ponytail: default the integration "on" for every test in this class (real
		# TaskPilot Settings.enabled is off on a fresh site); tests that care about the
		# disabled path override this locally.
		patcher = patch("erpnext.projects.doctype.task.task.is_enabled", return_value=True)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_load_maps_fields(self, mock_get_client):
		_mock(mock_get_client)
		doc = frappe.get_doc("Task", "WEBSITE-12")
		self.assertEqual(doc.subject, "Design homepage")
		self.assertEqual(doc.status, "Working")
		self.assertEqual(doc.project, "WEBSITE")

	def test_insert_requires_project(self, mock_get_client):
		_mock(mock_get_client)
		doc = frappe.get_doc({"doctype": "Task", "subject": "x"})
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_insert_names_by_sequence(self, mock_get_client):
		c = _mock(mock_get_client)
		c.create_work_item.return_value = dict(fixtures.WORK_ITEM, sequence_id=13)
		doc = frappe.get_doc({"doctype": "Task", "subject": "x", "project": "WEBSITE"})
		# ponytail: only Task's client is mocked here; skip the Project link's own
		# existence check (real network path, exercised separately in test_virtual_project).
		doc.insert(ignore_links=True)
		self.assertEqual(doc.name, "WEBSITE-13")

	def test_delete_moves_to_cancelled_state(self, mock_get_client):
		c = _mock(mock_get_client)
		frappe.get_doc("Task", "WEBSITE-12").delete()
		c.update_work_item.assert_called_with("WEBSITE-12", {"state": "s-cancel"})

	def test_get_list_filters_status(self, mock_get_client):
		_mock(mock_get_client)
		from erpnext.projects.doctype.task.task import Task

		rows = Task.get_list(
			{
				"filters": [["Task", "project", "=", "WEBSITE"], ["Task", "status", "=", "Working"]],
				"page_length": 20,
			}
		)
		self.assertEqual([r.name for r in rows], ["WEBSITE-12"])

		# negative: the fixture's only item is Working, so filtering for Completed finds nothing
		rows = Task.get_list(
			{
				"filters": [["Task", "project", "=", "WEBSITE"], ["Task", "status", "=", "Completed"]],
				"page_length": 20,
			}
		)
		self.assertEqual(rows, [])

	@patch("erpnext.projects.doctype.task.task.is_enabled", return_value=False)
	def test_get_list_aggregate_count_disabled(self, mock_is_enabled, mock_get_client):
		from erpnext.projects.doctype.task.task import Task

		rows = Task.get_list({"fields": [{"COUNT": "*", "as": "count"}], "filters": []})
		self.assertEqual(rows[0].count, 0)

	def test_get_list_aggregate_count_enabled(self, mock_get_client):
		_mock(mock_get_client)
		from erpnext.projects.doctype.task.task import Task

		rows = Task.get_list({"fields": [{"COUNT": "*", "as": "count"}], "filters": []})
		self.assertEqual(rows[0].count, 1)

	def test_get_list_as_list_for_link_search(self, mock_get_client):
		_mock(mock_get_client)
		from erpnext.projects.doctype.task.task import Task

		rows = Task.get_list(
			{
				"or_filters": [["Task", "subject", "like", "%homepage%"]],
				"as_list": True,
				"page_length": 20,
			}
		)
		self.assertEqual(rows, [["WEBSITE-12", "Design homepage"]])
