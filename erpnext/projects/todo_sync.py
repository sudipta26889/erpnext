import frappe

from erpnext.projects.taskpilot_client import TaskPilotError, get_client


def push_assignees(doc, method=None):
	"""Mirror ERPNext assignments (ToDo) on Task docs to TaskPilot work-item assignees."""
	if frappe.flags.in_migrate or frappe.flags.in_install:
		return
	if doc.reference_type != "Task" or not doc.reference_name:
		return
	try:
		client = get_client()
		emails = frappe.get_all(
			"ToDo",
			filters={"reference_type": "Task", "reference_name": doc.reference_name, "status": "Open"},
			pluck="allocated_to",
		)
		by_email = {m.get("email"): m["id"] for m in client.members()}
		assignees = [by_email[e] for e in emails if e in by_email]
		client.update_work_item(doc.reference_name, {"assignees": assignees})
	except TaskPilotError:
		pass  # assignment mirroring is best-effort; never block a ToDo save
