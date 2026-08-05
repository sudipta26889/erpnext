from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase


class TestTimesheetTaskPilot(IntegrationTestCase):
	def test_set_project_derives_from_task_docname(self):
		row = frappe.new_doc("Timesheet Detail")
		row.task = "WEBSITE-12"
		row.set_project()
		self.assertEqual(row.project, "WEBSITE")

	def test_mismatched_project_throws(self):
		row = frappe.new_doc("Timesheet Detail")
		row.task, row.project, row.idx = "WEBSITE-12", "OTHERPROJ", 1
		self.assertRaises(frappe.ValidationError, row.validate_task_project)

	@patch("erpnext.projects.taskpilot_client.get_client")
	def test_completed_logs_push_done_state(self, mock_get_client):
		c = mock_get_client.return_value
		c.ensure_state.return_value = {"id": "s-done"}
		ts = frappe.new_doc("Timesheet")
		ts.docstatus = 1
		ts.append("time_logs", {"task": "WEBSITE-12", "completed": 1, "hours": 1})
		ts.update_task_and_project()
		c.update_work_item.assert_called_once_with("WEBSITE-12", {"state": "s-done"})
