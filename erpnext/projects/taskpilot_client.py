# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json
import time

import requests

import frappe
from frappe import _


class TaskPilotError(frappe.ValidationError):
	pass


def get_client() -> "TaskPilotClient":
	settings = frappe.get_cached_doc("TaskPilot Settings")
	if not settings.enabled:
		raise TaskPilotError(_("TaskPilot integration is disabled. Configure TaskPilot Settings first."))
	return TaskPilotClient(settings)


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
				time.sleep(min(int(resp.headers.get("X-RateLimit-Reset") or 5), 30))
				continue
			break
		if resp.status_code >= 400:
			detail = resp.json() if resp.content else {}
			raise TaskPilotError(
				_("TaskPilot API error {0} on {1} {2}: {3}").format(resp.status_code, method, url, detail)
			)
		if method != "GET":
			self.invalidate_cache()
		return resp.json() if resp.content else None

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
		frappe.cache.delete_keys(f"taskpilot|{self.slug}")
