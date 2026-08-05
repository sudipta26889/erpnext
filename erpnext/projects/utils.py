# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

# For license information, please see license.txt


import frappe


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def query_task(doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict):
	from erpnext.projects.doctype.task.task import Task
	from erpnext.projects.taskpilot_client import TaskPilotError

	task_filters = (
		[["Task", "project", "=", filters["project"]]] if filters and filters.get("project") else []
	)
	try:
		rows = Task.get_list({"filters": task_filters, "page_length": 10**6})
	except TaskPilotError:
		return []
	needle = (txt or "").lower()
	rows = [r for r in rows if needle in r.name.lower() or needle in (r.subject or "").lower()]
	return [[r.name, r.subject] for r in rows[int(start) : int(start) + int(page_len)]]
