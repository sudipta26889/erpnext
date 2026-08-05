# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

"""Money-path coverage for the local (non-TaskPilot) half of Timesheet: overlap validation
and billed-status rollups. Project is now a TaskPilot-backed virtual doctype (see
erpnext/projects/doctype/project/project.py), so the old
erpnext/projects/doctype/timesheet/test_timesheet.py fixture chain (deleted with the
timesheet local-rollup refactor, commit 2366374e8e) can no longer be reused as-is. This
rebuilds just the project/task-free slice of it (I9)."""

from datetime import timedelta

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from erpnext.projects.doctype.timesheet.timesheet import OverlapError


def update_activity_type(activity_type):
	doc = frappe.get_doc("Activity Type", activity_type)
	doc.billing_rate = 50.0
	doc.costing_rate = 20.0
	doc.save(ignore_permissions=True)


def make_timesheet(
	employee=None,
	hours=1.0,
	is_billable=0,
	activity_type="_Test Activity Type",
	project=None,
	task=None,
	company=None,
	user=None,
	do_not_submit=False,
	simulate=False,
):
	"""Minimal local-only Timesheet factory: a single time log, no Project/Task links
	required. `simulate=True` nudges the log forward on OverlapError instead of raising, the
	way the old fixture did, for tests that just want *a* valid timesheet."""
	update_activity_type(activity_type)
	timesheet = frappe.new_doc("Timesheet")
	timesheet.employee = employee
	timesheet.company = company or "_Test Company"
	timesheet.user = user
	detail = timesheet.append("time_logs", {})
	detail.is_billable = is_billable
	detail.activity_type = activity_type
	detail.from_time = now_datetime()
	detail.hours = hours
	detail.to_time = detail.from_time + timedelta(hours=hours)
	detail.project = project
	detail.task = task

	if simulate:
		while True:
			try:
				timesheet.save(ignore_permissions=True)
				break
			except OverlapError:
				detail.from_time = detail.from_time + timedelta(minutes=10)
				detail.to_time = detail.from_time + timedelta(hours=hours)
	else:
		timesheet.save(ignore_permissions=True)

	if not do_not_submit:
		timesheet.submit()

	return timesheet


class TestTimesheetCore(IntegrationTestCase):
	def setUp(self):
		# Respect (don't fight) Projects Settings: enforce the user-overlap check this test
		# exercises, and turn off the employee-overlap check since these timesheets carry no
		# employee (leaving it on would be a no-op here, but it's the honest, explicit toggle).
		settings = frappe.get_single("Projects Settings")
		settings.ignore_user_time_overlap = 0
		settings.ignore_employee_time_overlap = 1
		settings.save()

	def test_user_overlap_raises(self):
		# `user` is a Link to a real User doc; frappe.session.user (Administrator under
		# run-tests) is guaranteed to exist without pulling in more fixtures.
		user = frappe.session.user
		make_timesheet(user=user, do_not_submit=True)
		self.assertRaises(OverlapError, make_timesheet, user=user, do_not_submit=True)

	def test_billing_status_transitions_with_sales_invoice(self):
		ts = make_timesheet(hours=2.0, is_billable=1, do_not_submit=True)
		row0 = ts.time_logs[0]
		ts.append(
			"time_logs",
			{
				"is_billable": 1,
				"activity_type": "_Test Activity Type",
				"from_time": row0.to_time,
				"to_time": row0.to_time + timedelta(hours=2),
				"hours": 2,
				"billing_hours": 2,
				"billing_rate": row0.billing_rate,
				"billing_amount": row0.billing_amount,
			},
		)
		row1 = ts.time_logs[1]

		def recompute():
			ts.calculate_total_amounts()
			ts.calculate_percentage_billed()
			ts.set_status()

		recompute()
		self.assertEqual(ts.status, "Draft")

		row0.sales_invoice = "DUMMY-SI-0001"
		recompute()
		self.assertEqual(ts.status, "Partially Billed")

		row1.sales_invoice = "DUMMY-SI-0001"
		recompute()
		self.assertEqual(ts.status, "Billed")
