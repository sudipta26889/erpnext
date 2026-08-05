# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TaskPilotSettings(Document):
	def validate(self):
		has_credentials = (
			self.api_url and self.workspace_slug and self.get_password("api_key", raise_exception=False)
		)
		if self.enabled and not has_credentials:
			frappe.throw(_("API URL, Workspace Slug and API Key are required to enable TaskPilot."))
		if self.api_url:
			self.api_url = self.api_url.rstrip("/")


@frappe.whitelist()
def test_connection() -> dict:
	frappe.only_for(("System Manager", "Projects Manager"))
	from erpnext.projects.taskpilot_client import get_client

	client = get_client()
	me = client.request("GET", "/users/me/", workspace=False)
	return {"ok": True, "user": me.get("email") or me.get("display_name")}
