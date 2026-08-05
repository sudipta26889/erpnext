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

	@patch("erpnext.projects.doctype.task.task.is_enabled", return_value=False)
	def test_disabled_strict_raises_does_not_exist(self, mock_is_enabled, mock_get_client):
		with patch("erpnext.projects.doctype.task.task.is_lenient", return_value=False):
			self.assertRaises(frappe.DoesNotExistError, frappe.get_doc, "Task", "WEBSITE-12")

	@patch("erpnext.projects.doctype.task.task.is_enabled", return_value=False)
	def test_disabled_lenient_returns_stub(self, mock_is_enabled, mock_get_client):
		with patch("erpnext.projects.doctype.task.task.is_lenient", return_value=True):
			doc = frappe.get_doc("Task", "WEBSITE-12")
			self.assertEqual(doc.name, "WEBSITE-12")
			self.assertEqual(doc.project, "WEBSITE")
			self.assertEqual(doc.status, "Open")
			self.assertEqual(doc.docstatus, 0)

	def test_unreachable_lenient_returns_stub(self, mock_get_client):
		from erpnext.projects.taskpilot_client import TaskPilotError

		c = _mock(mock_get_client)
		c.get_work_item.side_effect = TaskPilotError("unreachable")
		with patch("erpnext.projects.doctype.task.task.is_lenient", return_value=True):
			doc = frappe.get_doc("Task", "WEBSITE-12")
			self.assertEqual(doc.name, "WEBSITE-12")
			self.assertEqual(doc.project, "WEBSITE")

	def test_unreachable_strict_propagates_error(self, mock_get_client):
		from erpnext.projects.taskpilot_client import TaskPilotError

		c = _mock(mock_get_client)
		c.get_work_item.side_effect = TaskPilotError("unreachable")
		with patch("erpnext.projects.doctype.task.task.is_lenient", return_value=False):
			self.assertRaises(TaskPilotError, frappe.get_doc, "Task", "WEBSITE-12")

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

	def test_get_list_date_range_filter_for_calendar(self, mock_get_client):
		# Shape frappe.desk.calendar.get_events / the Gantt view send: >=/<= on
		# exp_start_date/exp_end_date, no explicit page_length (I5).
		c = _mock(mock_get_client)
		other = dict(
			fixtures.WORK_ITEM,
			id="33333333-3333-3333-3333-333333333333",
			sequence_id=13,
			start_date="2026-09-01",
			target_date="2026-09-10",
		)
		c.list_work_items.return_value = [fixtures.WORK_ITEM, other]
		from erpnext.projects.doctype.task.task import Task

		rows = Task.get_list(
			{
				"filters": [
					["Task", "exp_start_date", "<=", "2026-08-25"],
					["Task", "exp_end_date", ">=", "2026-08-15"],
				]
			}
		)
		self.assertEqual([r.name for r in rows], ["WEBSITE-12"])

	def test_get_list_date_range_filter_with_pypika_term_fieldname(self, mock_get_client):
		# frappe.desk.calendar.get_events (frappe/desk/calendar.py, ~lines 63-70) builds its
		# date-range filter rows with the fieldname slot holding a PyPika term, not a string:
		#   functions.IfNull(dt[field_map.start], ValueWrapper("0001-01-01 00:00:00"))
		# Reproduce that exact shape (same frappe.query_builder pieces, not a stand-in) and
		# confirm the date window is still honored instead of every row being excluded.
		from frappe.query_builder import functions
		from frappe.query_builder.terms import ValueWrapper

		c = _mock(mock_get_client)
		other = dict(
			fixtures.WORK_ITEM,
			id="33333333-3333-3333-3333-333333333333",
			sequence_id=13,
			start_date="2026-09-01",
			target_date="2026-09-10",
		)
		c.list_work_items.return_value = [fixtures.WORK_ITEM, other]
		from erpnext.projects.doctype.task.task import Task

		dt = frappe.qb.DocType("Task")
		start_field = functions.IfNull(dt["exp_start_date"], ValueWrapper("0001-01-01 00:00:00"))
		end_field = functions.IfNull(dt["exp_end_date"], ValueWrapper("2199-12-31 00:00:00"))
		rows = Task.get_list(
			{
				"filters": [
					[start_field, "<=", "2026-08-25"],
					[end_field, ">=", "2026-08-15"],
				]
			}
		)
		self.assertEqual([r.name for r in rows], ["WEBSITE-12"])

	def test_get_list_date_range_filter_excludes_none_dates(self, mock_get_client):
		c = _mock(mock_get_client)
		no_dates = dict(
			fixtures.WORK_ITEM,
			id="44444444-4444-4444-4444-444444444444",
			sequence_id=14,
			start_date=None,
			target_date=None,
		)
		c.list_work_items.return_value = [no_dates]
		from erpnext.projects.doctype.task.task import Task

		rows = Task.get_list({"filters": [["Task", "exp_start_date", "<=", "2026-08-25"]]})
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
				"fields": ["name", "subject", {"IFNULL": ["locate('x', name)", -9999], "as": "_relevance"}],
				"as_list": True,
				"page_length": 20,
			}
		)
		self.assertEqual(rows, [["WEBSITE-12", "Design homepage", 0]])

		# Without the relevance dict in fields, rows have no trailing 0
		rows = Task.get_list(
			{
				"or_filters": [["Task", "subject", "like", "%homepage%"]],
				"as_list": True,
				"page_length": 20,
			}
		)
		self.assertEqual(rows, [["WEBSITE-12", "Design homepage"]])
