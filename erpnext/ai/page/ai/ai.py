# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe

from erpnext.ai import registry


@frappe.whitelist()
def get_boot_info() -> dict:
	"""Minimal state the SPA needs before it can render anything."""
	from erpnext.ai.paperclip import assert_ai_user

	assert_ai_user()
	settings = registry.settings()
	return {
		"enabled": bool(settings.enabled),
		"company": settings.erpnext_company,
		"agent_id": settings.agent_id,
	}
