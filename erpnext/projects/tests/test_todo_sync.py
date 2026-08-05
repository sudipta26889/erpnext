from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.projects.tests import fixtures
from erpnext.projects.todo_sync import push_assignees


class TestTodoSync(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Create test user for allocated_to field (ToDo requires an existing user)
		test_email = fixtures.MEMBERS[0]["email"]
		if not frappe.db.exists("User", test_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": test_email,
					"first_name": "Test Member",
					"disabled": 1,
				}
			).insert(ignore_permissions=True)

	@patch("erpnext.projects.doctype.task.task.Task.load_from_db")
	@patch("erpnext.projects.todo_sync.get_client")
	def test_open_todo_pushes_assignees(self, mock_get_client, mock_load_from_db):
		c = mock_get_client.return_value
		c.members.return_value = fixtures.MEMBERS
		# Mock Task.load_from_db to prevent actual loading
		mock_load_from_db.return_value = None
		todo = frappe.get_doc(
			{
				"doctype": "ToDo",
				"description": "x",
				"reference_type": "Task",
				"reference_name": "WEBSITE-12",
				"allocated_to": fixtures.MEMBERS[0]["email"],
			}
		)
		todo.flags.ignore_links = True
		todo.insert()
		c.update_work_item.assert_called_with("WEBSITE-12", {"assignees": ["u-1"]})
		# Clean up by directly deleting from DB to avoid Task load issues
		frappe.db.delete("ToDo", {"name": todo.name})

	@patch("erpnext.projects.todo_sync.get_client")
	def test_non_task_todo_is_ignored(self, mock_get_client):
		doc = frappe._dict(reference_type="Sales Order", reference_name="SO-1")
		push_assignees(doc)
		mock_get_client.assert_not_called()

	@patch("erpnext.projects.doctype.task.task.Task.load_from_db")
	@patch("erpnext.projects.todo_sync.get_client")
	def test_trash_excludes_deleted_todo(self, mock_get_client, mock_load_from_db):
		c = mock_get_client.return_value
		c.members.return_value = fixtures.MEMBERS
		mock_load_from_db.return_value = None
		todo = frappe.get_doc(
			{
				"doctype": "ToDo",
				"description": "x",
				"reference_type": "Task",
				"reference_name": "WEBSITE-12",
				"allocated_to": fixtures.MEMBERS[0]["email"],
			}
		)
		todo.flags.ignore_links = True
		todo.insert()
		c.reset_mock()
		push_assignees(todo, "on_trash")
		c.update_work_item.assert_called_with("WEBSITE-12", {"assignees": []})
		frappe.db.delete("ToDo", {"name": todo.name})
