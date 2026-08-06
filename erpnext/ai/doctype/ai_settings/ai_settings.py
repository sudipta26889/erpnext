# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document


class AISettings(Document):
	def validate(self):
		if self.paperclip_url:
			self.paperclip_url = self.paperclip_url.rstrip("/")

		self._validate_json_field("allowed_roles")
		self._validate_json_field("enabled_tools")
		self._validate_json_field("allowed_methods")

		if not self.enabled:
			return

		missing = [
			label
			for value, label in (
				(self.paperclip_url, _("Paperclip URL")),
				(self.paperclip_company_id, _("Paperclip Company ID")),
				(self.agent_id, _("CEO Agent ID")),
				(self.get_password("board_api_key", raise_exception=False), _("Board API Key")),
			)
			if not value
		]
		if missing:
			frappe.throw(_("Required to enable AI: {0}").format(", ".join(missing)))

		# Without a bound company every company-scoped query is ambiguous, and on a
		# multi-entity site it would mix separate legal entities' books.
		if not self.erpnext_company:
			frappe.throw(_("ERPNext Company is required to enable AI."))

	def _validate_json_field(self, fieldname: str) -> None:
		raw = (self.get(fieldname) or "").strip()
		if not raw:
			return
		try:
			value = json.loads(raw)
		except ValueError:
			frappe.throw(_("{0} must be valid JSON.").format(_(self.meta.get_label(fieldname))))
		if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
			frappe.throw(_("{0} must be a JSON list of strings.").format(_(self.meta.get_label(fieldname))))


@frappe.whitelist()
def test_connection() -> dict:
	frappe.only_for("System Manager")
	from erpnext.ai.paperclip import get_client

	return {"ok": True, "health": get_client().health()}
