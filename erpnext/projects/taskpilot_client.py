# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json
import time

import frappe
import requests
from frappe import _
from frappe.utils import cint


class TaskPilotError(frappe.ValidationError):
	pass


class TaskPilotNotFound(TaskPilotError):
	pass


def _parse_json(resp, url):
	"""Parse JSON response, raising TaskPilotError on invalid JSON."""
	try:
		return resp.json()
	except ValueError as e:
		raise TaskPilotError(
			_("TaskPilot returned a non-JSON response from {0}: {1}").format(url, resp.text[:500])
		) from e


def is_enabled() -> bool:
	return bool(frappe.get_cached_doc("TaskPilot Settings").enabled)


def get_client() -> "TaskPilotClient":
	settings = frappe.get_cached_doc("TaskPilot Settings")
	if not settings.enabled:
		raise TaskPilotError(_("TaskPilot integration is disabled. Configure TaskPilot Settings first."))
	return TaskPilotClient(settings)


def get_fallback_cost_center() -> str | None:
	"""Per-project cost centers are retired; every project now shares this fallback."""
	return frappe.get_cached_doc("TaskPilot Settings").default_cost_center


class TaskPilotClient:
	def __init__(self, settings):
		self.base_url = (settings.api_url or "").rstrip("/")
		self.slug = settings.workspace_slug
		self.api_key = settings.get_password("api_key", raise_exception=False)
		self.cache_ttl = settings.cache_ttl or 60
		self.lenient = bool(settings.lenient_link_validation)

	def request(self, method: str, path: str, params=None, payload=None, workspace: bool = True):
		prefix = f"/api/v1/workspaces/{self.slug}/" if workspace else "/api/v1/"
		url = self.base_url + prefix + path.lstrip("/")
		resp = None
		for attempt in range(2):
			try:
				resp = requests.request(
					method,
					url,
					headers={"X-Api-Key": self.api_key},
					params=params,
					json=payload,
					timeout=15,
				)
			except requests.RequestException as e:
				raise TaskPilotError(_("TaskPilot unreachable at {0}: {1}").format(self.base_url, e)) from e
			if resp.status_code == 429 and attempt == 0:
				# ponytail: one retry after the advertised reset window, capped at 30s
				wait = min(cint(resp.headers.get("X-RateLimit-Reset")) or 5, 30)
				time.sleep(wait)
				continue
			break
		if resp.status_code >= 400:
			try:
				detail = _parse_json(resp, url) if resp.content else {}
			except TaskPilotError:
				# If parsing failed, use raw text as detail
				detail = resp.text[:500]
			raise TaskPilotError(
				_("TaskPilot API error {0} on {1} {2}: {3}").format(resp.status_code, method, url, detail)
			)
		if method != "GET":
			self.invalidate_cache()
		return _parse_json(resp, url) if resp.content else None

	def _cache_key(self, path: str, params=None) -> str:
		return f"taskpilot|{self.slug}|{path}|{json.dumps(params or {}, sort_keys=True)}"

	def get_cached(self, path: str, params=None):
		key = self._cache_key(path, params)
		cached = frappe.cache.get_value(key)
		if cached is None:
			cached = self.request("GET", path, params=params)
			frappe.cache.set_value(key, cached, expires_in_sec=self.cache_ttl)
		return cached

	def get_paginated(self, path: str, params=None) -> list[dict]:
		params = dict(params or {}, per_page=100)
		results, cursor = [], None
		while True:
			page = self.get_cached(path, dict(params, cursor=cursor) if cursor else params)
			if isinstance(page, list):
				return page
			results.extend(page.get("results") or [])
			if not page.get("next_page_results"):
				return results
			cursor = page.get("next_cursor")

	def invalidate_cache(self):
		frappe.cache.delete_keys(f"taskpilot|{self.slug}|")

	# ---- projects ----

	def list_projects(self) -> list[dict]:
		return self.get_paginated("/projects/")

	def project_uuid(self, identifier: str) -> str:
		for p in self.list_projects():
			if p.get("identifier") == identifier:
				return p["id"]
		raise TaskPilotNotFound(
			_("TaskPilot project {0} not found in workspace {1}").format(identifier, self.slug)
		)

	def get_project(self, identifier: str) -> dict:
		return self.get_cached(f"/projects/{self.project_uuid(identifier)}/")

	def create_project(self, payload: dict) -> dict:
		return self.request("POST", "/projects/", payload=payload)

	def update_project(self, identifier: str, payload: dict) -> dict:
		return self.request("PATCH", f"/projects/{self.project_uuid(identifier)}/", payload=payload)

	def archive_project(self, identifier: str):
		self.request("POST", f"/projects/{self.project_uuid(identifier)}/archive/")

	# ---- work items ----

	def get_work_item(self, docname: str) -> dict:
		return self.get_cached(f"/work-items/{docname}/")

	def list_work_items(self, project_identifier: str) -> list[dict]:
		return self.get_paginated(f"/projects/{self.project_uuid(project_identifier)}/work-items/")

	def create_work_item(self, project_identifier: str, payload: dict) -> dict:
		return self.request(
			"POST", f"/projects/{self.project_uuid(project_identifier)}/work-items/", payload=payload
		)

	def update_work_item(self, docname: str, payload: dict) -> dict:
		wi = self.get_work_item(docname)
		return self.request("PATCH", f"/projects/{wi['project']}/work-items/{wi['id']}/", payload=payload)

	def work_item_identifier(self, project_identifier: str, uuid: str) -> str | None:
		for wi in self.list_work_items(project_identifier):
			if wi["id"] == uuid:
				return f"{project_identifier}-{wi['sequence_id']}"
		return None

	# ---- states / members ----

	def states(self, project_identifier: str) -> list[dict]:
		return self.get_paginated(f"/projects/{self.project_uuid(project_identifier)}/states/")

	def ensure_state(self, project_identifier: str, name: str, group: str) -> dict:
		for s in self.states(project_identifier):
			if s["name"] == name:
				return s
		return self.request(
			"POST",
			f"/projects/{self.project_uuid(project_identifier)}/states/",
			payload={"name": name, "group": group, "color": "#8b5cf6", "external_source": "erpnext"},
		)

	def members(self) -> list[dict]:
		return self.get_paginated("/members/")
