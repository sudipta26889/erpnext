# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Server-side Paperclip client.

Every call the SPA needs is proxied through a whitelisted method here rather
than issued from the browser, so the board API key never reaches the client and
each entry point re-checks the caller's role.
"""

import json

import frappe
import requests
from frappe import _

from erpnext.ai import registry

TIMEOUT = 30
STANDING_ISSUE_TITLE = "ERPNext Operations"


class PaperclipError(Exception):
	pass


class PaperclipClient:
	def __init__(self, base_url: str, api_key: str, company_id: str, agent_id: str):
		self.base_url = base_url.rstrip("/")
		self.api_key = api_key
		self.company_id = company_id
		self.agent_id = agent_id

	def request(
		self, method: str, path: str, json_body: dict | None = None, params: dict | None = None
	) -> dict:
		url = f"{self.base_url}/{path.lstrip('/')}"
		response = requests.request(
			method,
			url,
			headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
			json=json_body,
			params=params,
			timeout=TIMEOUT,
		)
		if response.status_code < 200 or response.status_code >= 300:
			raise PaperclipError(
				_("Paperclip {0} {1} failed ({2}): {3}").format(
					method, path, response.status_code, response.text[:300]
				)
			)
		try:
			return response.json()
		except ValueError:
			return {}

	def health(self) -> dict:
		return self.request("GET", "/api/health")


def get_client() -> PaperclipClient:
	settings = registry.settings()
	if not settings.enabled:
		raise PaperclipError(_("ERPNext AI is disabled in AI Settings."))
	key = settings.get_password("board_api_key", raise_exception=False)
	if not key:
		raise PaperclipError(_("No Paperclip board API key configured."))
	return PaperclipClient(settings.paperclip_url, key, settings.paperclip_company_id, settings.agent_id)


def assert_ai_user() -> None:
	"""Role gate for the AI surface.

	Under this architecture the agent is a single ERPNext identity regardless of
	who is chatting, so access is restricted to explicitly named roles rather
	than left open. See spec section 6.2.
	"""
	raw = (registry.settings().allowed_roles or '["System Manager"]').strip()
	try:
		allowed = json.loads(raw)
	except ValueError:
		allowed = ["System Manager"]

	if not set(allowed) & set(frappe.get_roles()):
		frappe.throw(_("You are not permitted to use the AI workspace."), frappe.PermissionError)


def _standing_issue(client: PaperclipClient) -> str:
	"""Find or create the standing conversation issue, mirroring how Paperclip's
	own board chat anchors on a 'Board Operations' issue."""
	issues = client.request(
		"GET", f"/api/companies/{client.company_id}/issues", params={"q": STANDING_ISSUE_TITLE}
	)
	rows = issues if isinstance(issues, list) else issues.get("issues") or []
	for row in rows:
		if row.get("title") == STANDING_ISSUE_TITLE and row.get("status") not in ("done", "cancelled"):
			return row["id"]

	created = client.request(
		"POST",
		f"/api/companies/{client.company_id}/issues",
		json_body={
			"title": STANDING_ISSUE_TITLE,
			"description": "Standing thread for the ERPNext desk AI tab.",
			"status": "todo",
			"priority": "medium",
			"assigneeAgentId": client.agent_id,
		},
	)
	return created["id"]


@frappe.whitelist()
def get_thread() -> dict:
	"""Comments plus live run state for the standing issue."""
	assert_ai_user()
	client = get_client()
	issue_id = _standing_issue(client)
	return {
		"issue_id": issue_id,
		"comments": client.request("GET", f"/api/issues/{issue_id}/comments", params={"order": "asc"}),
		"live_runs": client.request("GET", f"/api/issues/{issue_id}/live-runs"),
	}


@frappe.whitelist(methods=["POST"])
def send_message(message: str) -> dict:
	"""Post a message. This enqueues an agent wake (wakeReason 'issue_commented')."""
	assert_ai_user()
	client = get_client()
	issue_id = _standing_issue(client)
	client.request("POST", f"/api/issues/{issue_id}/comments", json_body={"body": message})
	return {"issue_id": issue_id, "sent": True}


@frappe.whitelist()
def get_run_events(run_id: str, after_seq: int = 0) -> dict:
	"""Cursor-based incremental run feed. Runs have no SSE, so the UI polls this."""
	assert_ai_user()
	return {
		"events": get_client().request(
			"GET", f"/api/heartbeat-runs/{run_id}/events", params={"afterSeq": int(after_seq)}
		)
	}


@frappe.whitelist()
def list_approvals() -> dict:
	assert_ai_user()
	client = get_client()
	return {
		"action_requests": client.request("GET", f"/api/companies/{client.company_id}/tools/action-requests")
	}


@frappe.whitelist(methods=["POST"])
def resolve_approval(action_request_id: str, approve: bool) -> dict:
	assert_ai_user()
	verb = "approve" if approve else "decline"
	get_client().request("POST", f"/api/tool-gateway/action-requests/{action_request_id}/{verb}")
	return {"id": action_request_id, "resolved": verb}
