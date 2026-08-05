import frappe
from frappe.desk.doctype.todo.todo import ToDo

from erpnext.projects.taskpilot_client import TaskPilotError, get_client


class CustomToDo(ToDo):
	"""frappe core's update_in_reference() raw-writes `_assign` to `tab{reference_type}` and only
	tolerates a missing table during `frappe.flags.in_install`. Virtual doctypes (Project/Task)
	have no such table once the migration patch drops tabProject/tabTask - skip the raw write for
	those; push_assignees (registered as this doctype's on_update/on_trash hook) is the real
	assignee mirror for TaskPilot-backed doctypes."""

	def update_in_reference(self):
		if self.reference_type and frappe.get_meta(self.reference_type).is_virtual:
			return
		super().update_in_reference()


def push_assignees(doc, method=None):
	"""Mirror ERPNext assignments (ToDo) on Task docs to TaskPilot work-item assignees."""
	if frappe.flags.in_migrate or frappe.flags.in_install:
		return
	if doc.reference_type != "Task" or not doc.reference_name:
		return
	try:
		client = get_client()
		filters = {"reference_type": "Task", "reference_name": doc.reference_name, "status": "Open"}
		if method == "on_trash":
			# on_trash fires before the row is deleted; exclude it from the aggregation
			filters["name"] = ["!=", doc.name]
		emails = frappe.get_all(
			"ToDo",
			filters=filters,
			pluck="allocated_to",
		)
		by_email = {m.get("email"): m["id"] for m in client.members()}
		assignees = [by_email[e] for e in emails if e in by_email]
		client.update_work_item(doc.reference_name, {"assignees": assignees})
	except TaskPilotError:
		pass  # assignment mirroring is best-effort; never block a ToDo save
