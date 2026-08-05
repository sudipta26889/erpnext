# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.utils import getdate, today

STATUS_TO_STATE = {
	"Open": ("Todo", "unstarted"),
	"Working": ("In Progress", "started"),
	"Pending Review": ("Pending Review", "started"),
	"Completed": ("Done", "completed"),
	"Cancelled": ("Cancelled", "cancelled"),
}

PRIORITY_TO_TP = {"Low": "low", "Medium": "medium", "High": "high", "Urgent": "urgent"}
PRIORITY_FROM_TP = {"low": "Low", "medium": "Medium", "high": "High", "urgent": "Urgent", "none": "Low"}

OPEN_STATUSES = ("Open", "Working", "Pending Review")


def state_to_status(state: dict, work_item: dict) -> str:
	group = (state or {}).get("group")
	if group == "started":
		status = "Pending Review" if state.get("name") == "Pending Review" else "Working"
	elif group == "completed":
		status = "Completed"
	elif group == "cancelled":
		status = "Cancelled"
	else:  # backlog, unstarted, triage, unknown
		status = "Open"

	target = work_item.get("target_date")
	if status in OPEN_STATUSES and target and getdate(target) < getdate(today()):
		status = "Overdue"
	return status


def work_item_to_task(wi, states_by_id, project_identifier, parent_docname=None, assignees=None):
	state = states_by_id.get(wi.get("state")) or {}
	return frappe._dict(
		doctype="Task",
		name=f"{project_identifier}-{wi['sequence_id']}",
		subject=wi.get("name"),
		description=wi.get("description_html"),
		status=state_to_status(state, wi),
		priority=PRIORITY_FROM_TP.get(wi.get("priority") or "none", "Low"),
		exp_start_date=wi.get("start_date"),
		exp_end_date=wi.get("target_date"),
		parent_task=parent_docname,
		project=project_identifier,
		completed_on=getdate(wi["completed_at"]) if wi.get("completed_at") else None,
		creation=wi.get("created_at"),
		modified=wi.get("updated_at"),
		modified_by=None,
		owner=None,
		docstatus=0,
		idx=wi.get("sequence_id") or 0,
		_assign=frappe.as_json(assignees) if assignees else None,
	)


def task_to_work_item_payload(doc, state_id=None, parent_uuid=None) -> dict:
	payload = {
		"name": doc.subject,
		"description_html": doc.description or "<p></p>",
		"priority": PRIORITY_TO_TP.get(doc.priority, "none"),
		"start_date": str(doc.exp_start_date) if doc.exp_start_date else None,
		"target_date": str(doc.exp_end_date) if doc.exp_end_date else None,
		"external_source": "erpnext",
	}
	if state_id:
		payload["state"] = state_id
	if parent_uuid:
		payload["parent"] = parent_uuid
	return payload


def project_to_payload(doc) -> dict:
	return {
		"name": doc.project_name,
		"identifier": doc.name or make_identifier(doc.project_name),
		"description": frappe.utils.strip_html(doc.notes or ""),
		"external_source": "erpnext",
		"external_id": doc.get("external_id") or doc.name,
	}


def tp_to_project(data: dict) -> frappe._dict:
	return frappe._dict(
		doctype="Project",
		name=data.get("identifier"),
		project_name=data.get("name"),
		notes=data.get("description_html") or data.get("description"),
		status="Completed" if data.get("archived_at") else "Open",
		is_active="No" if data.get("archived_at") else "Yes",
		priority="Medium",
		creation=data.get("created_at"),
		modified=data.get("updated_at"),
		docstatus=0,
		idx=0,
	)


def make_identifier(project_name: str) -> str:
	return re.sub(r"[^A-Za-z0-9]", "", project_name or "").upper()[:10] or "PROJ"
