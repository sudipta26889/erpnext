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
