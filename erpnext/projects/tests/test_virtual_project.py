# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.projects.tests import fixtures


def _mock_client(mock_get_client):
	c = mock_get_client.return_value
	c.list_projects.return_value = [fixtures.PROJECT]
	c.get_project.return_value = fixtures.PROJECT
	c.list_work_items.return_value = [fixtures.WORK_ITEM]
	c.states.return_value = fixtures.STATES
	return c


@patch("erpnext.projects.doctype.project.project.get_client")
class TestVirtualProject(IntegrationTestCase):
	def setUp(self):
		# ponytail: default the integration "on" for every test in this class (real
		# TaskPilot Settings.enabled is off on a fresh site); tests that care about the
		# disabled path override this locally.
		patcher = patch("erpnext.projects.doctype.project.project.is_enabled", return_value=True)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_get_doc_loads_from_taskpilot(self, mock_get_client):
		_mock_client(mock_get_client)
		doc = frappe.get_doc("Project", "WEBSITE")
		self.assertEqual(doc.project_name, "Website Revamp")
		self.assertEqual(doc.status, "Open")
		self.assertEqual(doc.percent_complete, 0.0)
		self.assertEqual(doc.total_sales_amount or 0, 0)

	def test_insert_creates_and_names_by_identifier(self, mock_get_client):
		c = _mock_client(mock_get_client)
		c.create_project.return_value = dict(fixtures.PROJECT, identifier="NEWPROJ")
		doc = frappe.get_doc({"doctype": "Project", "project_name": "New Proj"})
		doc.insert()
		self.assertEqual(doc.name, "NEWPROJ")
		payload = c.create_project.call_args[0][0]
		self.assertEqual(payload["external_source"], "erpnext")

	def test_get_list_filters_by_text(self, mock_get_client):
		_mock_client(mock_get_client)
		from erpnext.projects.doctype.project.project import Project

		rows = Project.get_list(
			{"filters": [["Project", "project_name", "like", "%revamp%"]], "page_length": 20}
		)
		self.assertEqual(len(rows), 1)

	def test_autoname_uses_identifier(self, mock_get_client):
		c = _mock_client(mock_get_client)
		c.create_project.side_effect = lambda payload: dict(
			fixtures.PROJECT, identifier=payload["identifier"]
		)
		doc = frappe.get_doc({"doctype": "Project", "project_name": "Website Revamp 2026!"})
		doc.insert()
		payload = c.create_project.call_args[0][0]
		self.assertEqual(payload["identifier"], "WEBSITEREV")
		self.assertEqual(doc.name, "WEBSITEREV")

	@patch("erpnext.projects.doctype.project.project.is_enabled", return_value=False)
	def test_get_list_returns_empty_when_disabled(self, mock_is_enabled, mock_get_client):
		from erpnext.projects.doctype.project.project import Project

		self.assertEqual(Project.get_list({}), [])
		self.assertEqual(Project.get_count({}), 0)

	def test_get_list_status_filter(self, mock_get_client):
		c = _mock_client(mock_get_client)
		archived = dict(fixtures.PROJECT, identifier="ARCHIVED", archived_at="2026-08-01T00:00:00Z")
		c.list_projects.return_value = [fixtures.PROJECT, archived]
		from erpnext.projects.doctype.project.project import Project

		args = {"filters": [["Project", "status", "=", "Open"]], "page_length": 20}
		rows = Project.get_list(args)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].name, fixtures.PROJECT["identifier"])
		self.assertEqual(Project.get_count(args), 1)

	def test_missing_project_raises_does_not_exist(self, mock_get_client):
		from erpnext.projects.taskpilot_client import TaskPilotNotFound

		c = _mock_client(mock_get_client)
		c.get_project.side_effect = TaskPilotNotFound("not found")
		self.assertRaises(frappe.DoesNotExistError, frappe.get_doc, "Project", "NOPE")

	def test_negative_status_filter_is_ignored(self, mock_get_client):
		c = _mock_client(mock_get_client)
		archived = dict(fixtures.PROJECT, identifier="ARCHIVED", archived_at="2026-08-01T00:00:00Z")
		c.list_projects.return_value = [fixtures.PROJECT, archived]
		from erpnext.projects.doctype.project.project import Project

		# != operator is unsupported; both rows should be returned (no filtering)
		args = {"filters": [["Project", "status", "!=", "Cancelled"]], "page_length": 20}
		rows = Project.get_list(args)
		self.assertEqual(len(rows), 2)

	def test_get_list_as_list_for_link_search(self, mock_get_client):
		_mock_client(mock_get_client)
		from erpnext.projects.doctype.project.project import Project

		rows = Project.get_list(
			{
				"or_filters": [["Project", "project_name", "like", "%revamp%"]],
				"fields": [
					"name",
					"project_name",
					{"IFNULL": ["locate('x', name)", -9999], "as": "_relevance"},
				],
				"as_list": True,
				"page_length": 20,
			}
		)
		self.assertEqual(rows, [["WEBSITE", "Website Revamp", 0]])

		# Without the relevance dict in fields, rows have no trailing 0
		rows = Project.get_list(
			{
				"or_filters": [["Project", "project_name", "like", "%revamp%"]],
				"as_list": True,
				"page_length": 20,
			}
		)
		self.assertEqual(rows, [["WEBSITE", "Website Revamp"]])

	def test_get_list_aggregate_count(self, mock_get_client):
		c = _mock_client(mock_get_client)
		archived = dict(fixtures.PROJECT, identifier="ARCHIVED", archived_at="2026-08-01T00:00:00Z")
		c.list_projects.return_value = [fixtures.PROJECT, archived]
		from erpnext.projects.doctype.project.project import Project

		# COUNT aggregate with alias "count"
		args = {"fields": [{"COUNT": "*", "as": "count"}], "filters": []}
		rows = Project.get_list(args)
		self.assertEqual(rows[0].count, 2)

		# COUNT aggregate when disabled
		with patch("erpnext.projects.doctype.project.project.is_enabled", return_value=False):
			rows = Project.get_list(args)
			self.assertEqual(rows[0].count, 0)
