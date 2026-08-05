import json
from unittest import TestCase

import frappe
from frappe.utils import add_days, today

from erpnext.projects import taskpilot_mapping as m
from erpnext.projects.tests import fixtures


class TestMapping(TestCase):
	def states_by_id(self):
		return {s["id"]: s for s in fixtures.STATES}

	def test_state_to_status_groups(self):
		wi = dict(fixtures.WORK_ITEM, target_date=None)
		self.assertEqual(m.state_to_status({"group": "unstarted", "name": "Todo"}, wi), "Open")
		self.assertEqual(m.state_to_status({"group": "started", "name": "In Progress"}, wi), "Working")
		self.assertEqual(
			m.state_to_status({"group": "started", "name": "Pending Review"}, wi), "Pending Review"
		)
		self.assertEqual(m.state_to_status({"group": "completed", "name": "Done"}, wi), "Completed")
		self.assertEqual(m.state_to_status({"group": "cancelled", "name": "Cancelled"}, wi), "Cancelled")

	def test_overdue_is_computed(self):
		wi = dict(fixtures.WORK_ITEM, target_date=add_days(today(), -1))
		self.assertEqual(m.state_to_status({"group": "started", "name": "In Progress"}, wi), "Overdue")
		self.assertEqual(m.state_to_status({"group": "completed", "name": "Done"}, wi), "Completed")

	def test_work_item_to_task_docname_and_fields(self):
		d = m.work_item_to_task(fixtures.WORK_ITEM, self.states_by_id(), "WEBSITE")
		self.assertEqual(d.name, "WEBSITE-12")
		self.assertEqual(d.subject, "Design homepage")
		self.assertEqual(d.status, "Working")
		self.assertEqual(d.priority, "High")
		self.assertEqual(d.project, "WEBSITE")
		self.assertEqual(d.exp_end_date, "2026-08-20")

	def test_task_payload_round_trip(self):
		doc = frappe._dict(
			subject="Design homepage",
			description="<p>Hero</p>",
			priority="Urgent",
			exp_start_date="2026-08-10",
			exp_end_date="2026-08-20",
			name="WEBSITE-12",
		)
		payload = m.task_to_work_item_payload(doc, state_id="s-progress", parent_uuid=None)
		self.assertEqual(payload["name"], "Design homepage")
		self.assertEqual(payload["priority"], "urgent")
		self.assertEqual(payload["state"], "s-progress")
		self.assertEqual(payload["external_source"], "erpnext")
		self.assertNotIn("parent", payload)

	def test_make_identifier(self):
		self.assertEqual(m.make_identifier("Website Revamp 2026!"), "WEBSITEREV")
		self.assertEqual(m.make_identifier("ab"), "AB")

	def test_tp_to_project_status(self):
		d = m.tp_to_project(fixtures.PROJECT)
		self.assertEqual(d.name, "WEBSITE")
		self.assertEqual(d.status, "Open")
		self.assertEqual(
			m.tp_to_project(dict(fixtures.PROJECT, archived_at="2026-08-04")).status, "Completed"
		)
