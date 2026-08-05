# ERPNext Projects on TaskPilot Virtual Backend — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert ERPNext's `Project` and `Task` doctypes to Frappe Virtual DocTypes whose sole storage is the dedicated "ERPNext" workspace in the user's self-hosted TaskPilot, keeping the desk UI intact and Timesheets/billing local.

**Architecture:** A cached REST client (`X-Api-Key`, rate-limit aware) talks to TaskPilot's public v1 API. Pure mapping functions translate between ERPNext fields and TaskPilot work items/projects/states. The virtual controllers implement Frappe's virtual-doctype contract (`load_from_db`, `db_insert`, `db_update`, `delete`, `get_list`, `get_count`); Project financial totals merge on read from local SQL. A migration patch pushes existing rows to TaskPilot, remaps link columns, and drops `tabProject`/`tabTask`.

**Tech Stack:** Frappe v17 (virtual doctypes, `frappe.qb`, Redis cache via `frappe.cache`), `requests`, TaskPilot REST v1 (OpenAPI at `https://taskpilot-api.sudiptadhara.in/api/schema/`).

**Spec:** `docs/superpowers/specs/2026-08-05-taskpilot-virtual-backend-design.md` — read it first.

## Global Constraints

- Branch: `feature/taskpilot-virtual-backend`. Commit per task, semantic messages (commitlint enforced), never commit to `develop` (pre-commit blocks it).
- Python: tabs, line length 110, ruff (`.pre-commit-config.yaml`). All `@frappe.whitelist()` methods MUST have typed parameters (`hooks.py:746 require_type_annotated_api_methods = True`).
- No raw SQL — use `frappe.qb` (pre-commit `postgres-compat` gate). Exception: the migration patch may read the legacy `tabProject`/`tabTask` tables with `frappe.db.sql` (patches dir is excluded from the gate) because the doctypes are already virtual when post-sync patches run.
- TaskPilot rules: no hard delete of work items (move to Cancelled state); project delete = archive; descriptions are sanitized HTML; timestamps UTC ISO 8601.
- Rate budget: 60 req/min (default key) / 300 (service token). Every read path must go through the client cache.
- Status↔state mapping (fixed by spec §3.4): Open↔Todo(unstarted), Working↔In Progress(started), Pending Review↔custom "Pending Review"(started), Completed↔Done(completed), Cancelled↔Cancelled(cancelled); Overdue is computed on read, never stored; Template ceases to exist.
- Priority mapping: Low↔low, Medium↔medium, High↔high, Urgent↔urgent; TaskPilot `none` → Low.
- Dropped in phase 1 (spec §3.7): Project Template(+Task), Project Update, Project User (portal is phase 2; TaskPilot members replace it), Task Depends On, Dependent Task, task weights/progress, `Project.customer` (no TaskPilot home — `validate_proj_cust` is removed), per-project `cost_center` (settings-level fallback instead), collect-progress emails, holiday-aware task scheduling.
- Test scope: the new `erpnext/projects/tests/` suite plus modified-module tests are maintained. The broader upstream suite references Project/Task fixtures and is expected to be red after conversion; triaging it is out of phase-1 scope (fork decision recorded in spec).
- Tests run on a bench site: `bench --site <site> run-tests --app erpnext --module <module>`. Task 0 establishes the site. Mock-client tests must not hit the network.

---

### Task 0: Environment + contract verification (no code changes)

**Files:** none created; verification only.

**Interfaces:**
- Produces: a working bench site named `tp.localhost` (or an agreed existing site) with this app installed; confirmation of the installed Frappe virtual-doctype hook signatures.

- [ ] **Step 1: Confirm bench + site.** If no bench exists, from the bench parent directory run:

```bash
bench init tp-bench --frappe-branch develop && cd tp-bench
bench get-app erpnext /mnt/projects/erpnext
bench new-site tp.localhost --admin-password admin --db-root-password <root-pw>
bench --site tp.localhost install-app erpnext
```

Expected: site boots (`bench --site tp.localhost console` opens).

- [ ] **Step 2: Verify the virtual contract in the installed Frappe.**

```bash
grep -n "def load_from_db\|def db_insert\|def db_update\|def get_list\|is_virtual" \
  apps/frappe/frappe/model/document.py apps/frappe/frappe/model/virtual_doctype.py 2>/dev/null | head -30
```

Expected: `virtual_doctype.py` (or equivalents in `document.py`) shows `load_from_db(self)`, `db_insert(self, *args, **kwargs)`, `db_update(self, *args, **kwargs)`, `delete(self, *args, **kwargs)` instance hooks and static `get_list(args)`, `get_count(args)`, `get_stats(args)`. If signatures differ, note the deltas — Tasks 6–7 must match the installed signatures, not this plan's assumption.

- [ ] **Step 3: Record credentials.** Obtain from the user: workspace slug, service-token API key. Do NOT commit them; they go into the TaskPilot Settings single on the site (Task 1) via the UI or:

```bash
bench --site tp.localhost console
>>> s = frappe.get_doc("TaskPilot Settings"); s.api_url = "https://taskpilot-api.sudiptadhara.in"; s.workspace_slug = "<slug>"; s.api_key = "<key>"; s.enabled = 1; s.save()
```

(Executable only after Task 1 exists — return here.)

---

### Task 1: `TaskPilot Settings` single

**Files:**
- Create: `erpnext/projects/doctype/taskpilot_settings/__init__.py` (empty)
- Create: `erpnext/projects/doctype/taskpilot_settings/taskpilot_settings.json`
- Create: `erpnext/projects/doctype/taskpilot_settings/taskpilot_settings.py`
- Test: `erpnext/projects/doctype/taskpilot_settings/test_taskpilot_settings.py`

**Interfaces:**
- Produces: doctype `TaskPilot Settings` (Single) with fields `enabled` (Check), `api_url` (Data), `workspace_slug` (Data), `api_key` (Password), `cache_ttl` (Int, default 60), `lenient_link_validation` (Check, default 1), `default_cost_center` (Link → Cost Center); whitelisted `test_connection() -> dict`.

- [ ] **Step 1: Write the doctype JSON.**

```json
{
 "actions": [],
 "creation": "2026-08-05 12:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": [
  "enabled", "api_url", "workspace_slug", "api_key", "column_break_1",
  "cache_ttl", "lenient_link_validation", "default_cost_center"
 ],
 "fields": [
  {"fieldname": "enabled", "fieldtype": "Check", "label": "Enabled", "default": "0"},
  {"fieldname": "api_url", "fieldtype": "Data", "label": "API URL",
   "description": "Base URL, e.g. https://taskpilot-api.sudiptadhara.in", "mandatory_depends_on": "enabled"},
  {"fieldname": "workspace_slug", "fieldtype": "Data", "label": "Workspace Slug", "mandatory_depends_on": "enabled"},
  {"fieldname": "api_key", "fieldtype": "Password", "label": "API Key", "mandatory_depends_on": "enabled"},
  {"fieldname": "column_break_1", "fieldtype": "Column Break"},
  {"fieldname": "cache_ttl", "fieldtype": "Int", "label": "Cache TTL (seconds)", "default": "60"},
  {"fieldname": "lenient_link_validation", "fieldtype": "Check", "label": "Lenient Link Validation",
   "default": "1", "description": "Warn instead of failing transaction saves when TaskPilot is unreachable"},
  {"fieldname": "default_cost_center", "fieldtype": "Link", "label": "Default Cost Center", "options": "Cost Center",
   "description": "Fallback cost center for project-linked transactions (replaces per-project cost center)"}
 ],
 "issingle": 1,
 "modified": "2026-08-05 12:00:00.000000",
 "module": "Projects",
 "name": "TaskPilot Settings",
 "owner": "Administrator",
 "permissions": [
  {"role": "Projects Manager", "read": 1, "write": 1},
  {"role": "System Manager", "read": 1, "write": 1}
 ],
 "sort_field": "creation",
 "sort_order": "DESC",
 "states": []
}
```

- [ ] **Step 2: Write the controller with `test_connection`.**

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TaskPilotSettings(Document):
	def validate(self):
		if self.enabled and not (self.api_url and self.workspace_slug and self.get_password("api_key", raise_exception=False)):
			frappe.throw(_("API URL, Workspace Slug and API Key are required to enable TaskPilot."))
		if self.api_url:
			self.api_url = self.api_url.rstrip("/")


@frappe.whitelist()
def test_connection() -> dict:
	from erpnext.projects.taskpilot_client import get_client

	client = get_client()
	me = client.request("GET", "/users/me/", workspace=False)
	return {"ok": True, "user": me.get("email") or me.get("display_name")}
```

- [ ] **Step 3: Write the failing test.**

```python
import frappe
from frappe.tests import IntegrationTestCase


class TestTaskPilotSettings(IntegrationTestCase):
	def test_enable_requires_credentials(self):
		settings = frappe.get_doc("TaskPilot Settings")
		settings.enabled = 1
		settings.api_url = ""
		self.assertRaises(frappe.ValidationError, settings.save)

	def test_url_trailing_slash_stripped(self):
		settings = frappe.get_doc("TaskPilot Settings")
		settings.enabled = 0
		settings.api_url = "https://example.test/"
		settings.save()
		self.assertEqual(settings.api_url, "https://example.test")
```

- [ ] **Step 4: Migrate + run.** `bench --site tp.localhost migrate` then
`bench --site tp.localhost run-tests --app erpnext --module erpnext.projects.doctype.taskpilot_settings.test_taskpilot_settings`
Expected: both tests PASS (`test_connection` is exercised in Task 2's tests via the client).

- [ ] **Step 5: Commit.** `git add erpnext/projects/doctype/taskpilot_settings && git commit -m "feat(projects): TaskPilot Settings single"`

---

### Task 2: REST client core — auth, errors, rate limit, cache

**Files:**
- Create: `erpnext/projects/taskpilot_client.py`
- Create: `erpnext/projects/tests/__init__.py` (empty)
- Test: `erpnext/projects/tests/test_taskpilot_client.py`

**Interfaces:**
- Consumes: `TaskPilot Settings` fields from Task 1.
- Produces: `TaskPilotError(frappe.ValidationError)`; `get_client() -> TaskPilotClient` (throws when disabled); `TaskPilotClient.request(method: str, path: str, params=None, payload=None, workspace=True) -> dict | list | None`; `get_cached(path, params=None)`; `get_paginated(path, params=None) -> list[dict]`; `invalidate_cache()`.

- [ ] **Step 1: Write the failing tests (mocked `requests`).**

```python
import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.projects.taskpilot_client import TaskPilotClient, TaskPilotError, get_client


def _resp(status=200, body=None, headers=None):
	m = MagicMock()
	m.status_code = status
	m.headers = headers or {}
	m.content = json.dumps(body).encode() if body is not None else b""
	m.json.return_value = body
	return m


def _client():
	settings = frappe._dict(
		api_url="https://tp.test", workspace_slug="erpnext", cache_ttl=60, lenient_link_validation=1
	)
	settings.get_password = lambda *a, **kw: "KEY"
	return TaskPilotClient(settings)


class TestTaskPilotClient(IntegrationTestCase):
	def setUp(self):
		frappe.cache.delete_keys("taskpilot|")

	def test_get_client_throws_when_disabled(self):
		frappe.db.set_single_value("TaskPilot Settings", "enabled", 0)
		self.assertRaises(TaskPilotError, get_client)

	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_request_builds_workspace_url_and_auth(self, mock_req):
		mock_req.return_value = _resp(body={"ok": 1})
		out = _client().request("GET", "/projects/")
		self.assertEqual(out, {"ok": 1})
		args, kwargs = mock_req.call_args
		self.assertEqual(args, ("GET", "https://tp.test/api/v1/workspaces/erpnext/projects/"))
		self.assertEqual(kwargs["headers"]["X-Api-Key"], "KEY")

	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_request_error_surfaces_detail(self, mock_req):
		mock_req.return_value = _resp(status=400, body={"error": "Bad state"})
		self.assertRaises(TaskPilotError, _client().request, "POST", "/projects/", payload={})

	@patch("erpnext.projects.taskpilot_client.time.sleep")
	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_429_backs_off_once_then_succeeds(self, mock_req, mock_sleep):
		mock_req.side_effect = [
			_resp(status=429, headers={"X-RateLimit-Reset": "3"}),
			_resp(body=[]),
		]
		self.assertEqual(_client().request("GET", "/projects/"), [])
		mock_sleep.assert_called_once_with(3)

	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_get_cached_hits_network_once(self, mock_req):
		mock_req.return_value = _resp(body={"results": []})
		c = _client()
		c.get_cached("/projects/")
		c.get_cached("/projects/")
		self.assertEqual(mock_req.call_count, 1)

	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_get_paginated_follows_cursor(self, mock_req):
		mock_req.side_effect = [
			_resp(body={"results": [{"id": 1}], "next_page_results": True, "next_cursor": "20:1:0"}),
			_resp(body={"results": [{"id": 2}], "next_page_results": False}),
		]
		out = _client().get_paginated("/projects/x/work-items/")
		self.assertEqual([r["id"] for r in out], [1, 2])
```

- [ ] **Step 2: Run to verify failure.**
`bench --site tp.localhost run-tests --app erpnext --module erpnext.projects.tests.test_taskpilot_client`
Expected: FAIL — `ModuleNotFoundError: erpnext.projects.taskpilot_client`.

- [ ] **Step 3: Implement the client core.**

```python
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
					method, url,
					headers={"X-Api-Key": self.api_key},
					params=params, json=payload, timeout=15,
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
```

- [ ] **Step 4: Run tests.** Same command. Expected: all 7 PASS. (`test_get_client_throws_when_disabled` needs Task 1 migrated.)

- [ ] **Step 5: Commit.** `git add erpnext/projects/taskpilot_client.py erpnext/projects/tests && git commit -m "feat(projects): TaskPilot REST client with cache and rate-limit backoff"`

---

### Task 3: Client domain helpers — projects, work items, states, members

**Files:**
- Modify: `erpnext/projects/taskpilot_client.py` (append methods to `TaskPilotClient`)
- Test: `erpnext/projects/tests/test_taskpilot_client.py` (append)
- Create: `erpnext/projects/tests/fixtures.py`

**Interfaces:**
- Produces (all on `TaskPilotClient`): `list_projects() -> list[dict]`; `project_uuid(identifier: str) -> str` (raises `TaskPilotError` if unknown); `get_project(identifier: str) -> dict`; `create_project(payload: dict) -> dict`; `update_project(identifier: str, payload: dict) -> dict`; `archive_project(identifier: str)`; `get_work_item(docname: str) -> dict` (docname = `PROJ-12`); `list_work_items(project_identifier: str) -> list[dict]`; `create_work_item(project_identifier: str, payload: dict) -> dict`; `update_work_item(docname: str, payload: dict) -> dict`; `states(project_identifier: str) -> list[dict]`; `ensure_state(project_identifier: str, name: str, group: str) -> dict`; `members() -> list[dict]`; `work_item_identifier(project_identifier: str, uuid: str) -> str | None`.
- Produces: `fixtures.py` module-level dicts `PROJECT`, `WORK_ITEM`, `STATES`, `MEMBERS` mirroring real API payload shapes (fields per spec §2).

- [ ] **Step 1: Write `fixtures.py`** — trimmed but shape-faithful payloads:

```python
"""Recorded-shape TaskPilot API fixtures (fields per OpenAPI schema, values fake)."""

PROJECT = {
	"id": "11111111-1111-1111-1111-111111111111",
	"name": "Website Revamp",
	"identifier": "WEBSITE",
	"description": "",
	"archived_at": None,
	"external_source": "erpnext",
	"external_id": "PROJ-0001",
	"created_at": "2026-08-01T10:00:00Z",
	"updated_at": "2026-08-05T10:00:00Z",
}

STATES = [
	{"id": "s-backlog", "name": "Backlog", "group": "backlog", "default": False},
	{"id": "s-todo", "name": "Todo", "group": "unstarted", "default": True},
	{"id": "s-progress", "name": "In Progress", "group": "started", "default": False},
	{"id": "s-review", "name": "Pending Review", "group": "started", "default": False},
	{"id": "s-done", "name": "Done", "group": "completed", "default": False},
	{"id": "s-cancel", "name": "Cancelled", "group": "cancelled", "default": False},
]

WORK_ITEM = {
	"id": "22222222-2222-2222-2222-222222222222",
	"name": "Design homepage",
	"description_html": "<p>Hero + nav</p>",
	"priority": "high",
	"start_date": "2026-08-10",
	"target_date": "2026-08-20",
	"sequence_id": 12,
	"parent": None,
	"state": "s-progress",
	"project": "11111111-1111-1111-1111-111111111111",
	"completed_at": None,
	"external_source": "erpnext",
	"external_id": None,
	"created_at": "2026-08-02T09:00:00Z",
	"updated_at": "2026-08-05T09:30:00Z",
}

MEMBERS = [{"id": "u-1", "email": "sudiptai26.889@gmail.com", "display_name": "Sudipta"}]
```

- [ ] **Step 2: Append failing tests** (same mocked-`requests` style as Task 2; key behaviors):

```python
	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_project_uuid_resolves_from_list(self, mock_req):
		mock_req.return_value = _resp(body=[fixtures.PROJECT])
		c = _client()
		self.assertEqual(c.project_uuid("WEBSITE"), fixtures.PROJECT["id"])
		self.assertRaises(TaskPilotError, c.project_uuid, "NOPE")

	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_get_work_item_by_docname(self, mock_req):
		mock_req.return_value = _resp(body=fixtures.WORK_ITEM)
		_client().get_work_item("WEBSITE-12")
		self.assertIn("/workspaces/erpnext/work-items/WEBSITE-12/", mock_req.call_args[0][1])

	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_ensure_state_creates_missing(self, mock_req):
		mock_req.side_effect = [
			_resp(body=[s for s in fixtures.STATES if s["name"] != "Pending Review"]),
			_resp(body=[fixtures.PROJECT]),
			_resp(status=201, body=fixtures.STATES[3]),
		]
		out = _client().ensure_state("WEBSITE", "Pending Review", "started")
		self.assertEqual(out["name"], "Pending Review")
		self.assertEqual(mock_req.call_args[0][0], "POST")

	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_update_work_item_patches_by_uuid(self, mock_req):
		mock_req.side_effect = [_resp(body=fixtures.WORK_ITEM), _resp(body=fixtures.WORK_ITEM)]
		_client().update_work_item("WEBSITE-12", {"priority": "low"})
		method, url = mock_req.call_args[0]
		self.assertEqual(method, "PATCH")
		self.assertIn(f"/projects/{fixtures.PROJECT['id']}/work-items/{fixtures.WORK_ITEM['id']}/", url)
```

Run; expected: FAIL with `AttributeError` on the missing methods.

- [ ] **Step 3: Implement the domain methods** (append to `TaskPilotClient`):

```python
	# ---- projects ----

	def list_projects(self) -> list[dict]:
		return self.get_paginated("/projects/")

	def project_uuid(self, identifier: str) -> str:
		for p in self.list_projects():
			if p.get("identifier") == identifier:
				return p["id"]
		raise TaskPilotError(_("TaskPilot project {0} not found in workspace {1}").format(identifier, self.slug))

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
		return self.request(
			"PATCH", f"/projects/{wi['project']}/work-items/{wi['id']}/", payload=payload
		)

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
```

Note on `ensure_state` test ordering: `states()` resolves `project_uuid` first, so the mocked call order is projects-list → states-list → POST when the caches are cold; the test above seeds states first because `get_paginated`→`get_cached` runs before uuid resolution in the POST path. If the assertion order fights the cache, clear with `frappe.cache.delete_keys("taskpilot|")` between arrangements — `setUp` already does.

- [ ] **Step 4: Run tests** — all client tests PASS.
- [ ] **Step 5: Commit.** `git commit -am "feat(projects): TaskPilot client domain helpers"`

---

### Task 4: Pure mapping module

**Files:**
- Create: `erpnext/projects/taskpilot_mapping.py`
- Test: `erpnext/projects/tests/test_taskpilot_mapping.py`

**Interfaces:**
- Consumes: fixture shapes from Task 3.
- Produces: `STATUS_TO_STATE: dict[str, tuple[str, str]]` (status → (state name, group)); `PRIORITY_TO_TP: dict[str, str]`; `PRIORITY_FROM_TP: dict[str, str]`; `state_to_status(state: dict, work_item: dict) -> str` (Overdue-aware); `work_item_to_task(wi: dict, states_by_id: dict, project_identifier: str, parent_docname: str | None = None, assignees: list[str] | None = None) -> frappe._dict`; `task_to_work_item_payload(doc, state_id: str | None, parent_uuid: str | None) -> dict`; `project_to_payload(doc) -> dict`; `tp_to_project(data: dict) -> frappe._dict`; `make_identifier(project_name: str) -> str`.

- [ ] **Step 1: Write failing tests.**

```python
import json
from unittest import TestCase

import frappe
from frappe.utils import add_days, today

from erpnext.projects import taskpilot_mapping as m
from erpnext.projects.tests import fixtures


class TestMapping(TestCase):
	def states_by_id(self):
		return {s["id"]: s for s in fixtures.STATES}

	def test_state_to_status_groups(self):
		wi = dict(fixtures.WORK_ITEM, target_date=None)
		self.assertEqual(m.state_to_status({"group": "unstarted", "name": "Todo"}, wi), "Open")
		self.assertEqual(m.state_to_status({"group": "started", "name": "In Progress"}, wi), "Working")
		self.assertEqual(m.state_to_status({"group": "started", "name": "Pending Review"}, wi), "Pending Review")
		self.assertEqual(m.state_to_status({"group": "completed", "name": "Done"}, wi), "Completed")
		self.assertEqual(m.state_to_status({"group": "cancelled", "name": "Cancelled"}, wi), "Cancelled")

	def test_overdue_is_computed(self):
		wi = dict(fixtures.WORK_ITEM, target_date=add_days(today(), -1))
		self.assertEqual(m.state_to_status({"group": "started", "name": "In Progress"}, wi), "Overdue")
		self.assertEqual(m.state_to_status({"group": "completed", "name": "Done"}, wi), "Completed")

	def test_work_item_to_task_docname_and_fields(self):
		d = m.work_item_to_task(fixtures.WORK_ITEM, self.states_by_id(), "WEBSITE")
		self.assertEqual(d.name, "WEBSITE-12")
		self.assertEqual(d.subject, "Design homepage")
		self.assertEqual(d.status, "Working")
		self.assertEqual(d.priority, "High")
		self.assertEqual(d.project, "WEBSITE")
		self.assertEqual(d.exp_end_date, "2026-08-20")

	def test_task_payload_round_trip(self):
		doc = frappe._dict(
			subject="Design homepage", description="<p>Hero</p>", priority="Urgent",
			exp_start_date="2026-08-10", exp_end_date="2026-08-20", name="WEBSITE-12",
		)
		payload = m.task_to_work_item_payload(doc, state_id="s-progress", parent_uuid=None)
		self.assertEqual(payload["name"], "Design homepage")
		self.assertEqual(payload["priority"], "urgent")
		self.assertEqual(payload["state"], "s-progress")
		self.assertEqual(payload["external_source"], "erpnext")
		self.assertNotIn("parent", payload)

	def test_make_identifier(self):
		self.assertEqual(m.make_identifier("Website Revamp 2026!"), "WEBSITEREV")
		self.assertEqual(m.make_identifier("ab"), "AB")

	def test_tp_to_project_status(self):
		d = m.tp_to_project(fixtures.PROJECT)
		self.assertEqual(d.name, "WEBSITE")
		self.assertEqual(d.status, "Open")
		self.assertEqual(m.tp_to_project(dict(fixtures.PROJECT, archived_at="2026-08-04")).status, "Completed")
```

Run: `bench --site tp.localhost run-tests --app erpnext --module erpnext.projects.tests.test_taskpilot_mapping` → FAIL (module missing).

- [ ] **Step 2: Implement.**

```python
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
```

- [ ] **Step 3: Run tests** → all PASS.
- [ ] **Step 4: Commit.** `git add erpnext/projects/taskpilot_mapping.py erpnext/projects/tests/test_taskpilot_mapping.py && git commit -m "feat(projects): TaskPilot field mapping module"`

---

### Task 5: Financials-on-read module

**Files:**
- Create: `erpnext/projects/project_financials.py`
- Test: `erpnext/projects/tests/test_project_financials.py`

**Interfaces:**
- Consumes: nothing TaskPilot-side — pure local `frappe.qb` aggregates keyed by the project name string.
- Produces: `compute_financials(project: str) -> dict` with keys `total_costing_amount, total_billable_amount, actual_time, actual_start_date, actual_end_date, total_purchase_cost, total_sales_amount, total_billed_amount, total_consumed_material_cost, gross_margin, per_gross_margin`.

The queries are lifted from the current `project.py` (`update_costing` `:324-352`, `set_consumed_material_cost` `:170-202`, `get_billed_amount_from_parent/child` `:385-413`, `calculate_total_purchase_cost` `:848-857`, `update_sales_amount` `:371-380`) — same logic, returning a dict instead of mutating a document.

- [ ] **Step 1: Write failing test** (empty DB ⇒ zeros; shape check):

```python
import frappe
from frappe.tests import IntegrationTestCase

from erpnext.projects.project_financials import compute_financials


class TestProjectFinancials(IntegrationTestCase):
	def test_empty_project_yields_zeroes(self):
		out = compute_financials("NO-SUCH-PROJECT")
		for key in (
			"total_costing_amount", "total_billable_amount", "actual_time", "total_purchase_cost",
			"total_sales_amount", "total_billed_amount", "total_consumed_material_cost",
			"gross_margin", "per_gross_margin",
		):
			self.assertEqual(out[key] or 0, 0, key)
		self.assertIsNone(out["actual_start_date"])
```

Run → FAIL (module missing).

- [ ] **Step 2: Implement** — move the five query blocks verbatim from `project.py` into module functions `_timesheet_totals(project)`, `_purchase_cost(project)`, `_sales_amount(project)`, `_billed_amount(project)`, `_consumed_material_cost(project)`, then:

```python
def compute_financials(project: str) -> dict:
	ts = _timesheet_totals(project)
	out = {
		"total_costing_amount": flt(ts.base_costing_amount),
		"total_billable_amount": flt(ts.base_billing_amount),
		"actual_time": flt(ts.time),
		"actual_start_date": ts.start_date,
		"actual_end_date": ts.end_date,
		"total_purchase_cost": _purchase_cost(project),
		"total_sales_amount": _sales_amount(project),
		"total_billed_amount": _billed_amount(project),
		"total_consumed_material_cost": _consumed_material_cost(project),
	}
	expense = out["total_costing_amount"] + out["total_purchase_cost"] + out["total_consumed_material_cost"]
	out["gross_margin"] = flt(out["total_billed_amount"]) - expense
	out["per_gross_margin"] = (
		(out["gross_margin"] / out["total_billed_amount"]) * 100 if out["total_billed_amount"] else 0
	)
	return out
```

(Each `_fn` is the exact `frappe.qb` query currently in `project.py` at the line ranges above, with `self.name` replaced by the `project` argument — copy them across, do not rewrite.)

- [ ] **Step 3: Run test** → PASS. **Step 4: Commit** `git add erpnext/projects/project_financials.py erpnext/projects/tests/test_project_financials.py && git commit -m "feat(projects): compute project financial totals on read"`

---

### Task 6: Virtual Project

**Files:**
- Modify: `erpnext/projects/doctype/project/project.json` (add `"is_virtual": 1`; remove fields listed below)
- Modify: `erpnext/projects/doctype/project/project.py` (full rewrite)
- Modify: `erpnext/projects/doctype/project/project.js` (trim buttons)
- Test: `erpnext/projects/tests/test_virtual_project.py`

**Interfaces:**
- Consumes: `get_client()`, client project methods (Task 3); `project_to_payload`, `tp_to_project`, `make_identifier` (Task 4); `compute_financials` (Task 5).
- Produces: virtual `Project` document; `Project.get_list(args) -> list[dict]`; whitelisted `update_costing_and_billing(project: str) -> dict` retained as a no-op alias returning fresh computed values (form JS still calls it).

- [ ] **Step 1: JSON edits.** In `project.json`: add `"is_virtual": 1` top-level. Remove these fields from `fields`/`field_order` (spec §3.7 drops their features): `customer`, `sales_order`, `project_template`, `copied_from`, `cost_center`, `holiday_list`, `department`, `users`, `estimated_costing` stays, all `collect_progress` block fields (`collect_progress`, `frequency`, `from_time`, `to_time`, `first_email`, `second_email`, `daily_time_to_send`, `day_to_send`, `weekly_time_to_send`, `message`), `percent_complete_method` (percent-complete is computed one way now). Keep: `naming_series` (unused but harmless), `project_name`, `status`, `project_type`? — **remove `project_type`** (no TaskPilot home). Keep the dates and all read-only financial fields.

- [ ] **Step 2: Rewrite `project.py`.**

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext.projects.project_financials import compute_financials
from erpnext.projects.taskpilot_client import get_client
from erpnext.projects.taskpilot_mapping import make_identifier, project_to_payload, tp_to_project


class Project(Document):
	def load_from_db(self):
		client = get_client()
		d = tp_to_project(client.get_project(self.name))
		d.update(compute_financials(self.name))
		d["percent_complete"] = compute_percent_complete(self.name, client=client)
		super(Document, self).__init__(d)

	def db_insert(self, *args, **kwargs):
		client = get_client()
		payload = project_to_payload(self)
		if not payload.get("identifier"):
			payload["identifier"] = make_identifier(self.project_name)
		created = client.create_project(payload)
		self.name = created["identifier"]

	def db_update(self, *args, **kwargs):
		client = get_client()
		client.update_project(self.name, {"name": self.project_name, "description": self.notes or ""})
		before = self.get_doc_before_save()
		if self.status in ("Completed", "Cancelled") and (not before or before.status == "Open"):
			client.archive_project(self.name)

	def delete(self, *args, **kwargs):
		get_client().archive_project(self.name)

	@staticmethod
	def get_list(args):
		client = get_client()
		rows = [tp_to_project(p) for p in client.list_projects()]
		txt = _like_value(args, ("name", "project_name"))
		if txt:
			rows = [r for r in rows if txt.lower() in (r.project_name or "").lower() or txt.lower() in r.name.lower()]
		start = int(args.get("start") or args.get("limit_start") or 0)
		length = int(args.get("page_length") or args.get("limit_page_length") or 20)
		return rows[start : start + length]

	@staticmethod
	def get_count(args):
		return len(get_client().list_projects())

	@staticmethod
	def get_stats(args):
		return {}


def compute_percent_complete(project: str, client=None) -> float:
	client = client or get_client()
	items = client.list_work_items(project)
	if not items:
		return 0.0
	states = {s["id"]: s for s in client.states(project)}
	done = sum(1 for wi in items if states.get(wi.get("state"), {}).get("group") in ("completed", "cancelled"))
	return round(done / len(items) * 100, 2)


def _like_value(args, fieldnames: tuple) -> str | None:
	filters = args.get("filters") or []
	if isinstance(filters, dict):
		filters = [[k, "=", v] for k, v in filters.items()]
	for f in filters:
		row = f if len(f) == 3 else f[1:]
		if row[0] in fieldnames and row[1] in ("like", "="):
			return str(row[2]).strip("%")
	return None


@frappe.whitelist()
def update_costing_and_billing(project: str) -> dict:
	"""Kept for the existing form button; totals are computed on read now."""
	return compute_financials(project)


@frappe.whitelist()
def get_cost_center_name(project: str) -> str | None:
	"""Per-project cost centers are retired; return the configured fallback."""
	return frappe.get_cached_doc("TaskPilot Settings").default_cost_center
```

Delete everything else in the old file (template copying, reminders, collect status, portal list context, kanban helper, `set_project_status`, `create_duplicate_project`, `get_users_for_project`, `get_timeline_data`, `hourly_reminder` etc.) — Task 10 removes their hook/JS callers. `get_users_for_project` removal requires dropping the `users` link-query block in `project.js` `onload` (Step 3).

- [ ] **Step 3: Trim `project.js`.** Remove: the `users.user` query block (`onload`), `sales_order` query block, the Actions buttons "Duplicate Project with Tasks", "Update Costing and Billing" → keep as read-only refresh calling `update_costing_and_billing` and reloading, "Set Project Status" block, `collect_progress` handler, and the `make_methods` entries for Purchase Order/Receipt/Invoice stay (they only seed `project` on new docs). Keep Gantt/Kanban View buttons (Kanban: replace `create_kanban_board_if_not_exists` call with plain route to Task list kanban filtered by project — the helper is deleted).

- [ ] **Step 4: Write tests** (mock at client boundary):

```python
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.projects.tests import fixtures


def _mock_client(mock_get_client):
	c = mock_get_client.return_value
	c.list_projects.return_value = [fixtures.PROJECT]
	c.get_project.return_value = fixtures.PROJECT
	c.list_work_items.return_value = [fixtures.WORK_ITEM]
	c.states.return_value = fixtures.STATES
	return c


@patch("erpnext.projects.doctype.project.project.get_client")
class TestVirtualProject(IntegrationTestCase):
	def test_get_doc_loads_from_taskpilot(self, mock_get_client):
		_mock_client(mock_get_client)
		doc = frappe.get_doc("Project", "WEBSITE")
		self.assertEqual(doc.project_name, "Website Revamp")
		self.assertEqual(doc.status, "Open")
		self.assertEqual(doc.percent_complete, 0.0)
		self.assertEqual(doc.total_sales_amount or 0, 0)

	def test_insert_creates_and_names_by_identifier(self, mock_get_client):
		c = _mock_client(mock_get_client)
		c.create_project.return_value = dict(fixtures.PROJECT, identifier="NEWPROJ")
		doc = frappe.get_doc({"doctype": "Project", "project_name": "New Proj"})
		doc.insert()
		self.assertEqual(doc.name, "NEWPROJ")
		payload = c.create_project.call_args[0][0]
		self.assertEqual(payload["external_source"], "erpnext")

	def test_get_list_filters_by_text(self, mock_get_client):
		_mock_client(mock_get_client)
		from erpnext.projects.doctype.project.project import Project

		rows = Project.get_list({"filters": [["Project", "project_name", "like", "%revamp%"]], "page_length": 20})
		self.assertEqual(len(rows), 1)
```

- [ ] **Step 5: Migrate + run** `bench --site tp.localhost migrate && bench --site tp.localhost run-tests --app erpnext --module erpnext.projects.tests.test_virtual_project` → PASS. Also delete the obsolete `erpnext/projects/doctype/project/test_project.py` (table-based) in this commit.
- [ ] **Step 6: Commit.** `git commit -am "feat(projects)!: Project becomes a TaskPilot-backed virtual doctype"`

---

### Task 7: Virtual Task

**Files:**
- Modify: `erpnext/projects/doctype/task/task.json` (add `"is_virtual": 1`, remove `"is_tree"`, drop fields below)
- Modify: `erpnext/projects/doctype/task/task.py` (full rewrite)
- Modify: `erpnext/projects/doctype/task/task_list.js`, `task_tree.js` (keep; they call the same endpoints)
- Test: `erpnext/projects/tests/test_virtual_task.py`

**Interfaces:**
- Consumes: client work-item/state methods (Task 3); mapping functions (Task 4).
- Produces: virtual `Task`; whitelisted `get_children(doctype: str, parent: str | None = None, task: str | None = None, project: str | None = None, is_root: bool = False) -> list[dict]` (same signature the tree view already calls); whitelisted `add_node()` and `add_multiple_tasks(data: str | list, parent: str)` preserved; `set_multiple_status(names: str | list, status: str)` preserved.

- [ ] **Step 1: JSON edits.** Add `"is_virtual": 1`; remove `"is_tree": 1` and fields: `lft`, `rgt`, `old_parent`, `is_group` (hierarchy no longer needs group flags — any task can parent), `is_template`, `template_task`, `start`, `duration`, `is_milestone` stays (label-mapped), `depends_on`, `depends_on_tasks`, `issue`, `type`, `color`, `task_weight`, `progress`, `review_date`, `department`, `company`, `total_costing_amount`, `total_billing_amount`, `actual_time`, `act_start_date`, `act_end_date` (timesheet actuals live on reports now), `expected_time`. Remove `"Template"` and keep `"Overdue"` in the `status` options list.

- [ ] **Step 2: Rewrite `task.py`.**

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext.projects.taskpilot_client import get_client
from erpnext.projects.taskpilot_mapping import (
	STATUS_TO_STATE,
	task_to_work_item_payload,
	work_item_to_task,
)


class Task(Document):
	def load_from_db(self):
		client = get_client()
		wi = client.get_work_item(self.name)
		project_identifier = self.name.rsplit("-", 1)[0]
		states = {s["id"]: s for s in client.states(project_identifier)}
		parent_docname = (
			client.work_item_identifier(project_identifier, wi["parent"]) if wi.get("parent") else None
		)
		emails = _assignee_emails(client, wi)
		super(Document, self).__init__(
			work_item_to_task(wi, states, project_identifier, parent_docname, emails)
		)

	def validate(self):
		self.validate_from_to_dates("exp_start_date", "exp_end_date")

	def db_insert(self, *args, **kwargs):
		client = get_client()
		if not self.project:
			frappe.throw(_("Task needs a Project (TaskPilot work items live inside a project)."))
		created = client.create_work_item(self.project, self._payload(client))
		self.name = f"{self.project}-{created['sequence_id']}"

	def db_update(self, *args, **kwargs):
		client = get_client()
		client.update_work_item(self.name, self._payload(client))

	def delete(self, *args, **kwargs):
		# TaskPilot has no hard delete: move to the cancelled-group state instead
		client = get_client()
		project_identifier = self.name.rsplit("-", 1)[0]
		state = client.ensure_state(project_identifier, "Cancelled", "cancelled")
		client.update_work_item(self.name, {"state": state["id"]})

	def _payload(self, client) -> dict:
		project_identifier = self.project or self.name.rsplit("-", 1)[0]
		state_id = None
		if self.status and self.status not in ("Overdue",):
			name, group = STATUS_TO_STATE.get(self.status, ("Todo", "unstarted"))
			state_id = client.ensure_state(project_identifier, name, group)["id"]
		parent_uuid = client.get_work_item(self.parent_task)["id"] if self.parent_task else None
		return task_to_work_item_payload(self, state_id=state_id, parent_uuid=parent_uuid)

	@staticmethod
	def get_list(args):
		client = get_client()
		project = _filter_value(args, "project")
		projects = [project] if project else [p["identifier"] for p in client.list_projects()]
		rows = []
		for identifier in projects:
			states = {s["id"]: s for s in client.states(identifier)}
			uuid_to_docname = {}
			items = client.list_work_items(identifier)
			for wi in items:
				uuid_to_docname[wi["id"]] = f"{identifier}-{wi['sequence_id']}"
			for wi in items:
				parent_docname = uuid_to_docname.get(wi["parent"]) if wi.get("parent") else None
				rows.append(work_item_to_task(wi, states, identifier, parent_docname))
		rows = _apply_filters(rows, args)
		start = int(args.get("start") or args.get("limit_start") or 0)
		length = int(args.get("page_length") or args.get("limit_page_length") or 20)
		return rows[start : start + length]

	@staticmethod
	def get_count(args):
		return len(Task.get_list(dict(args, page_length=10**6, start=0)))

	@staticmethod
	def get_stats(args):
		return {}


def _assignee_emails(client, wi) -> list[str]:
	if not wi.get("assignees"):
		return []
	by_id = {m["id"]: m.get("email") for m in client.members()}
	return [e for e in (by_id.get(a) for a in wi["assignees"]) if e]


def _filter_value(args, fieldname: str):
	filters = args.get("filters") or []
	if isinstance(filters, dict):
		return filters.get(fieldname)
	for f in filters:
		row = f if len(f) == 3 else f[1:]
		if row[0] == fieldname and row[1] == "=":
			return row[2]
	return None


def _apply_filters(rows, args):
	status = _filter_value(args, "status")
	parent = _filter_value(args, "parent_task")
	subject = _filter_value(args, "subject") or _filter_value(args, "name")
	if status:
		wanted = status if isinstance(status, list | tuple) else [status]
		rows = [r for r in rows if r.status in wanted]
	if parent is not None:
		rows = [r for r in rows if (r.parent_task or "") == (parent or "")]
	if subject:
		needle = str(subject).strip("%").lower()
		rows = [r for r in rows if needle in (r.subject or "").lower() or needle in r.name.lower()]
	return rows


@frappe.whitelist()
def get_children(
	doctype: str,
	parent: str | None = None,
	task: str | None = None,
	project: str | None = None,
	is_root: bool = False,
) -> list[dict]:
	parent_task = task or (parent if parent and not is_root and parent != "All Tasks" else None)
	args = {"filters": [["Task", "parent_task", "=", parent_task or ""]], "page_length": 500}
	if project:
		args["filters"].append(["Task", "project", "=", project])
	rows = Task.get_list(args)
	children = frappe._dict()
	for r in Task.get_list({"filters": [["Task", "project", "=", project]] if project else [], "page_length": 10**6}):
		if r.parent_task:
			children[r.parent_task] = True
	return [
		{"value": r.name, "title": r.subject, "expandable": 1 if children.get(r.name) else 0} for r in rows
	]


@frappe.whitelist(methods=["POST"])
def add_node():
	from frappe.desk.treeview import make_tree_args

	args = frappe.form_dict
	args.update({"name_field": "subject"})
	args = make_tree_args(**args)
	if args.parent_task in ("All Tasks", args.project):
		args.parent_task = None
	frappe.get_doc(args).insert()


@frappe.whitelist(methods=["POST"])
def add_multiple_tasks(data: str | list, parent: str):
	data = frappe.parse_json(data)
	parent_doc = frappe.get_doc("Task", parent) if parent and parent != "All Tasks" else None
	for d in data:
		if not d.get("subject"):
			continue
		frappe.get_doc(
			{
				"doctype": "Task",
				"subject": d["subject"],
				"parent_task": parent_doc.name if parent_doc else None,
				"project": parent_doc.project if parent_doc else None,
			}
		).insert()


@frappe.whitelist(methods=["POST"])
def set_multiple_status(names: str | list, status: str):
	names = frappe.parse_json(names)
	for name in names:
		task = frappe.get_doc("Task", name)
		task.status = status
		task.save()
```

Also delete `erpnext/projects/doctype/task/test_task.py` (table-based) and the old `make_timesheet` mapper stays — re-add it verbatim from the old file (it maps fields only, no SQL):

```python
@frappe.whitelist()
def make_timesheet(source_name: str, target_doc=None, ignore_permissions: bool = False):
	from frappe.model.mapper import get_mapped_doc

	def set_missing_values(source, target):
		target.parent_project = source.project
		target.append("time_logs", {"project": source.project, "task": source.name})

	return get_mapped_doc(
		"Task", source_name, {"Task": {"doctype": "Timesheet"}}, target_doc,
		postprocess=set_missing_values, ignore_permissions=ignore_permissions,
	)
```

- [ ] **Step 3: Write tests.**

```python
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.projects.tests import fixtures


def _mock(mock_get_client):
	c = mock_get_client.return_value
	c.get_work_item.return_value = fixtures.WORK_ITEM
	c.states.return_value = fixtures.STATES
	c.list_projects.return_value = [fixtures.PROJECT]
	c.list_work_items.return_value = [fixtures.WORK_ITEM]
	c.members.return_value = fixtures.MEMBERS
	c.ensure_state.side_effect = lambda p, name, group: next(
		s for s in fixtures.STATES if s["name"] == name
	)
	return c


@patch("erpnext.projects.doctype.task.task.get_client")
class TestVirtualTask(IntegrationTestCase):
	def test_load_maps_fields(self, mock_get_client):
		_mock(mock_get_client)
		doc = frappe.get_doc("Task", "WEBSITE-12")
		self.assertEqual(doc.subject, "Design homepage")
		self.assertEqual(doc.status, "Working")
		self.assertEqual(doc.project, "WEBSITE")

	def test_insert_requires_project(self, mock_get_client):
		_mock(mock_get_client)
		doc = frappe.get_doc({"doctype": "Task", "subject": "x"})
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_insert_names_by_sequence(self, mock_get_client):
		c = _mock(mock_get_client)
		c.create_work_item.return_value = dict(fixtures.WORK_ITEM, sequence_id=13)
		doc = frappe.get_doc({"doctype": "Task", "subject": "x", "project": "WEBSITE"})
		doc.insert()
		self.assertEqual(doc.name, "WEBSITE-13")

	def test_delete_moves_to_cancelled_state(self, mock_get_client):
		c = _mock(mock_get_client)
		frappe.get_doc("Task", "WEBSITE-12").delete()
		c.update_work_item.assert_called_with("WEBSITE-12", {"state": "s-cancel"})

	def test_get_list_filters_status(self, mock_get_client):
		_mock(mock_get_client)
		from erpnext.projects.doctype.task.task import Task

		rows = Task.get_list(
			{"filters": [["Task", "project", "=", "WEBSITE"], ["Task", "status", "=", "Working"]], "page_length": 20}
		)
		self.assertEqual([r.name for r in rows], ["WEBSITE-12"])
```

Run `bench --site tp.localhost run-tests --app erpnext --module erpnext.projects.tests.test_virtual_task` → PASS.

- [ ] **Step 4: `task_list.js`/`task.js` trims.** Remove indicator entry for "Template", the `is_group` handler, template/`depends_on` link queries; keep Gantt popup and bulk status menu (they call `set_multiple_status`, preserved).

- [ ] **Step 5: Commit.** `git commit -am "feat(projects)!: Task becomes a TaskPilot-backed virtual doctype"`

---

### Task 8: Link queries and the `frappe.db.get_value("Project"/"Task", ...)` sweep

**Files:**
- Modify: `erpnext/controllers/queries.py:422-465` (`get_project_name`)
- Modify: `erpnext/projects/utils.py` (`query_task`)
- Modify: `erpnext/stock/get_item_details.py:1088-1089` (`get_default_cost_center`)
- Modify: `erpnext/selling/doctype/sales_order/sales_order.py:385-393` (`validate_proj_cust` — delete)
- Modify: `erpnext/selling/doctype/sales_order/mapper.py:325,503`, `erpnext/buying/doctype/purchase_order/mapper.py:159`, `erpnext/stock/doctype/pick_list/mapper.py:326`, `erpnext/subcontracting/doctype/subcontracting_receipt/subcontracting_receipt.py:310` (project cost-center reads)
- Test: extend `erpnext/projects/tests/test_virtual_project.py`

**Interfaces:**
- Consumes: `get_client()`, `TaskPilotError`, `Project.get_list`, `Task.get_list`.
- Produces: `get_project_name(doctype, txt, searchfield, start, page_len, filters) -> list[list]` (same whitelisted signature, API-backed); helper `erpnext.projects.taskpilot_client.get_fallback_cost_center() -> str | None`.

- [ ] **Step 1: Rewrite `get_project_name`** in `controllers/queries.py` — replace the `frappe.qb` body with:

```python
@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_project_name(doctype, txt, searchfield, start, page_len, filters):
	from erpnext.projects.taskpilot_client import TaskPilotError, get_client
	from erpnext.projects.taskpilot_mapping import tp_to_project

	try:
		rows = [tp_to_project(p) for p in get_client().list_projects()]
	except TaskPilotError:
		return []
	needle = (txt or "").lower()
	rows = [r for r in rows if needle in r.name.lower() or needle in (r.project_name or "").lower()]
	return [[r.name, r.project_name] for r in rows[int(start) : int(start) + int(page_len)]]
```

(`filters` like `customer`/`company` are silently ignored now — projects no longer carry them.) Rewrite `erpnext/projects/utils.py::query_task` the same way over `Task.get_list` with a `project` filter passthrough, returning `[[name, subject], ...]`.

- [ ] **Step 2: Cost-center fallback.** Add to `taskpilot_client.py`:

```python
def get_fallback_cost_center() -> str | None:
	return frappe.get_cached_doc("TaskPilot Settings").default_cost_center
```

In `get_item_details.py` replace the project lookup block:

```python
# OLD
if args.get("project"):
	cost_center = frappe.db.get_value("Project", args.get("project"), "cost_center", cache=True)
# NEW
if args.get("project"):
	from erpnext.projects.taskpilot_client import get_fallback_cost_center
	cost_center = get_fallback_cost_center()
```

Apply the same one-line substitution at each mapper call site listed in **Files** (each currently reads `frappe.db.get_value("Project", ..., "cost_center")`).

- [ ] **Step 3: Delete `validate_proj_cust`** in `sales_order.py` and its call in `validate()`; projects have no customer.

- [ ] **Step 4: Sweep the remaining direct reads.** Run:

```bash
grep -rn "db.get_value(\"Project\"\|db.get_value(\"Task\"\|db.get_value('Project'\|db.get_value('Task'\|frappe.get_all(\"Task\"\|frappe.db.count(\"Task\"\|get_cached_doc(\"Project\"" erpnext --include="*.py" | grep -v "patches\|/tests/\|test_"
```

For every hit outside files already rewritten in Tasks 6–7: replace with a client call (`get_client().get_project(...)` / `.get_work_item(...)` wrapped in `try/except TaskPilotError` honoring `lenient_link_validation`) **or** delete the branch if it belongs to a dropped feature (template/holiday/collect-progress code paths). Record each decision in the commit message body. Expected remaining hits after this step: zero.

- [ ] **Step 5: Test + commit.** Add a test asserting `get_project_name` returns `[["WEBSITE", "Website Revamp"]]` under the mocked client. Run the projects test modules → PASS. `git commit -am "refactor!: route project/task lookups through TaskPilot client"`

---

### Task 9: Timesheet rework

**Files:**
- Modify: `erpnext/projects/doctype/timesheet/timesheet.py` (`update_task_and_project` `:169-191`, `get_projectwise_timesheet_data` stays — local SQL)
- Modify: `erpnext/projects/doctype/timesheet_detail/timesheet_detail.py` (`set_project` `:55-58`, `validate_task_project` `:119-128`)
- Test: `erpnext/projects/tests/test_timesheet_taskpilot.py`

**Interfaces:**
- Consumes: `get_client()`, `TaskPilotError`, `STATUS_TO_STATE`.
- Produces: Timesheet submit moves fully-completed tasks' work items to the Done state; no writes to Project/Task documents remain.

- [ ] **Step 1: Rewrite `update_task_and_project`:**

```python
	def update_task_and_project(self):
		"""Push completion state to TaskPilot; totals are computed on read now."""
		from erpnext.projects.taskpilot_client import TaskPilotError, get_client
		from erpnext.projects.taskpilot_mapping import STATUS_TO_STATE

		seen = set()
		for data in self.time_logs:
			if not data.task or data.task in seen:
				continue
			seen.add(data.task)
			completed = all(tl.completed for tl in self.time_logs if tl.task == data.task)
			status = "Completed" if completed and self.docstatus == 1 else "Working"
			name, group = STATUS_TO_STATE[status]
			try:
				client = get_client()
				project_identifier = data.task.rsplit("-", 1)[0]
				state = client.ensure_state(project_identifier, name, group)
				client.update_work_item(data.task, {"state": state["id"]})
			except TaskPilotError as e:
				frappe.msgprint(str(e), indicator="orange", alert=True)
```

- [ ] **Step 2: Rework `TimesheetDetail.set_project` and `validate_task_project`:**

```python
	def set_project(self):
		if self.task and not self.project:
			self.project = self.task.rsplit("-", 1)[0]

	def validate_task_project(self):
		if self.task and self.project and self.task.rsplit("-", 1)[0] != self.project:
			frappe.throw(
				_("Row {0}: Task {1} does not belong to Project {2}").format(
					self.idx, frappe.bold(self.task), frappe.bold(self.project)
				)
			)
```

(The docname convention `PROJECT-seq` makes both checks local string operations — zero API calls in the hot save path.)

- [ ] **Step 3: Tests.**

```python
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.projects.doctype.timesheet_detail.timesheet_detail import TimesheetDetail
from erpnext.projects.tests import fixtures


class TestTimesheetTaskPilot(IntegrationTestCase):
	def test_set_project_derives_from_task_docname(self):
		row = frappe.new_doc("Timesheet Detail")
		row.task = "WEBSITE-12"
		row.set_project()
		self.assertEqual(row.project, "WEBSITE")

	def test_mismatched_project_throws(self):
		row = frappe.new_doc("Timesheet Detail")
		row.task, row.project, row.idx = "WEBSITE-12", "OTHERPROJ", 1
		self.assertRaises(frappe.ValidationError, row.validate_task_project)

	@patch("erpnext.projects.taskpilot_client.get_client")
	def test_completed_logs_push_done_state(self, mock_get_client):
		c = mock_get_client.return_value
		c.ensure_state.return_value = {"id": "s-done"}
		ts = frappe.new_doc("Timesheet")
		ts.docstatus = 1
		ts.append("time_logs", {"task": "WEBSITE-12", "completed": 1, "hours": 1})
		ts.update_task_and_project()
		c.update_work_item.assert_called_once_with("WEBSITE-12", {"state": "s-done"})
```

Run `bench --site tp.localhost run-tests --app erpnext --module erpnext.projects.tests.test_timesheet_taskpilot` → PASS. (Timesheet→Sales Invoice needs no change — `make_sales_invoice` copies `parent_project` as a string; leave its existing behavior untested here.)

- [ ] **Step 4: Commit.** `git commit -am "refactor(projects)!: timesheets push state to TaskPilot, no local rollups"`

---

### Task 10: Sales Order mapper + assignee push

**Files:**
- Modify: `erpnext/selling/doctype/sales_order/mapper.py:158-181` (`make_project`)
- Modify: `erpnext/hooks.py` (`doc_events`: add ToDo)
- Create: `erpnext/projects/todo_sync.py`
- Test: `erpnext/projects/tests/test_todo_sync.py`

**Interfaces:**
- Consumes: virtual Project insert (Task 6), client members/work-item methods (Task 3).
- Produces: `erpnext.projects.todo_sync.push_assignees(doc, method=None)` wired to `ToDo` `on_update` and `on_trash`.

- [ ] **Step 1: Simplify `make_project`** — the mapper now only seeds name and dates (no customer/sales_order/cost-center fields on Project):

```python
@frappe.whitelist()
def make_project(source_name: str, target_doc=None):
	def set_missing_values(source, target):
		# spec §3.6: the reverse SO pointer rides TaskPilot's external_id
		target.external_id = source.name

	doc = get_mapped_doc(
		"Sales Order",
		source_name,
		{
			"Sales Order": {
				"doctype": "Project",
				"field_map": {"name": "project_name", "delivery_date": "expected_end_date"},
			}
		},
		target_doc,
		postprocess=set_missing_values,
	)
	return doc
```

(`project_to_payload` already forwards `doc.get("external_id")`, so the TaskPilot project records which Sales Order spawned it.)

- [ ] **Step 2: `todo_sync.py`:**

```python
import frappe

from erpnext.projects.taskpilot_client import TaskPilotError, get_client


def push_assignees(doc, method=None):
	"""Mirror ERPNext assignments (ToDo) on Task docs to TaskPilot work-item assignees."""
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
```

In `hooks.py` `doc_events` add:

```python
	"ToDo": {
		"on_update": "erpnext.projects.todo_sync.push_assignees",
		"on_trash": "erpnext.projects.todo_sync.push_assignees",
	},
```

- [ ] **Step 3: Tests.**

```python
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.projects.tests import fixtures
from erpnext.projects.todo_sync import push_assignees


class TestTodoSync(IntegrationTestCase):
	@patch("erpnext.projects.todo_sync.get_client")
	def test_open_todo_pushes_assignees(self, mock_get_client):
		c = mock_get_client.return_value
		c.members.return_value = fixtures.MEMBERS
		todo = frappe.get_doc(
			{
				"doctype": "ToDo", "description": "x", "reference_type": "Task",
				"reference_name": "WEBSITE-12", "allocated_to": fixtures.MEMBERS[0]["email"],
			}
		).insert()
		c.update_work_item.assert_called_with("WEBSITE-12", {"assignees": ["u-1"]})
		todo.delete()

	@patch("erpnext.projects.todo_sync.get_client")
	def test_non_task_todo_is_ignored(self, mock_get_client):
		doc = frappe._dict(reference_type="Sales Order", reference_name="SO-1")
		push_assignees(doc)
		mock_get_client.assert_not_called()
```

Run `bench --site tp.localhost run-tests --app erpnext --module erpnext.projects.tests.test_todo_sync` → PASS.
- [ ] **Step 4: Commit.** `git commit -am "feat(projects): SO->project mapper trim + ToDo assignee mirroring"`

---

### Task 11: Cleanup — hooks, workspace, dead doctypes, reports

**Files:**
- Modify: `erpnext/hooks.py` (scheduler `:482,489-490,502,509-510`; portal `:220-221,237`; `has_website_permission` `:335`)
- Modify: `erpnext/startup/notifications.py:13-14,40`
- Modify: `erpnext/projects/workspace/projects/projects.json`, `erpnext/workspace_sidebar/projects.json`
- Create: `erpnext/patches/v16_0/drop_taskpilot_replaced_doctypes.py`
- Modify: `erpnext/patches.txt` (append below `[post_model_sync]`)
- Delete: `erpnext/projects/doctype/{project_template,project_template_task,project_update,project_user,task_depends_on,dependent_task}/` directories, `erpnext/projects/report/{project_summary,delayed_tasks_summary,project_wise_stock_tracking}/` (phase 2 rewrites), `erpnext/templates/pages/projects.*`, `erpnext/templates/includes/projects/`, `erpnext/config/projects.py`, `erpnext/projects/web_form/tasks/`, `erpnext/projects/doctype/project/project_timesheet.js`

**Interfaces:** none new; this task deletes.

- [ ] **Step 1: hooks.py.** Remove the five project scheduler entries and `set_tasks_as_overdue`; remove website route rules `/project`, `/tasks` and the "Projects" portal menu item; remove `Project` from `has_website_permission` (keep Timesheet). Keep `calendars = ["Task", ...]`.
- [ ] **Step 2: notifications.py.** Keep Task/Project open-count entries **only if** their `get_list` path is acceptable at login frequency — they query counts on desk load. Decision: remove both (Project + Task) to protect the rate budget; keep Timesheet Draft count (local).
- [ ] **Step 3: Workspace JSONs.** Remove links/cards for Project Template, Project Type (doctype survives? **No** — `project_type` field was dropped; delete the Project Type card link too, leave the doctype in place untouched for phase-2 reconsideration), Project Update, and the three deleted reports; keep Project, Task, Timesheet, Activity Type/Cost, Daily Timesheet Summary, Timesheet Billing Summary, Projects Settings + add TaskPilot Settings link.
- [ ] **Step 4: Deletion patch.**

```python
import frappe


def execute():
	for doctype in (
		"Project Template",
		"Project Template Task",
		"Project Update",
		"Project User",
		"Task Depends On",
		"Dependent Task",
	):
		frappe.delete_doc("DocType", doctype, ignore_missing=True, force=True)
	for report in ("Project Summary", "Delayed Tasks Summary", "Project wise Stock Tracking"):
		frappe.delete_doc("Report", report, ignore_missing=True, force=True)
```

Append to `patches.txt`: `erpnext.patches.v16_0.drop_taskpilot_replaced_doctypes`.

- [ ] **Step 5: Delete the listed directories/files**, run `bench --site tp.localhost migrate`, then `grep -rn "Project Template\|Project Update\|Task Depends On\|project_user" erpnext --include="*.py" --include="*.js" --include="*.json" | grep -v patches | grep -v node_modules` — fix any residual references (expected: the two dashboard chart JSONs under `erpnext/projects/dashboard_chart/` referencing removed filters — update or delete them; `number_card/non_completed_tasks` keeps working via `get_list`... **Decision: delete the two dashboard charts and three number cards** — they query counts through the report/doctype layer on dashboard render; phase 2 restores them API-aware. Keep the workspace chart block removed accordingly).
- [ ] **Step 6: Migrate + boot check.** `bench --site tp.localhost migrate && bench --site tp.localhost execute frappe.ping` → site boots; desk loads Projects workspace without console errors.
- [ ] **Step 7: Commit.** `git commit -am "chore(projects)!: remove doctypes and surfaces replaced by TaskPilot"`

---

### Task 12: Migration patch — push legacy rows, remap links, drop tables

**Files:**
- Create: `erpnext/patches/v16_0/migrate_projects_to_taskpilot.py`
- Modify: `erpnext/patches.txt` (append AFTER the Task 11 patch line)
- Test: manual dry-run procedure (patch guards make it idempotent)

**Interfaces:**
- Consumes: client methods (Task 3), `make_identifier` (Task 4), `STATUS_TO_STATE` (Task 4).
- Produces: TaskPilot holds every legacy project/task (`external_id` = old docname); every link column re-points at new identifiers; `tabProject`/`tabTask` dropped.

- [ ] **Step 1: Write the patch.** Runs post-model-sync, so the doctypes are already virtual — read legacy tables with raw SQL (allowed here), push via client, remap, drop:

```python
import frappe

# Link columns that store a Project name (from the integration survey; child tables included)
PROJECT_LINK_COLUMNS = [
	("GL Entry", "project"), ("Payment Ledger Entry", "project"), ("Account Closing Balance", "project"),
	("Sales Invoice", "project"), ("Sales Invoice Item", "project"), ("Purchase Invoice", "project"),
	("Purchase Invoice Item", "project"), ("POS Invoice", "project"), ("POS Invoice Item", "project"),
	("Journal Entry Account", "project"), ("Payment Entry", "project"), ("Payment Request", "project"),
	("Sales Order", "project"), ("Sales Order Item", "project"), ("Delivery Note", "project"),
	("Delivery Note Item", "project"), ("Purchase Order", "project"), ("Purchase Order Item", "project"),
	("Purchase Receipt", "project"), ("Purchase Receipt Item", "project"), ("Supplier Quotation", "project"),
	("Supplier Quotation Item", "project"), ("Material Request Item", "project"), ("Stock Entry", "project"),
	("Stock Entry Detail", "project"), ("Stock Ledger Entry", "project"), ("Stock Reservation Entry", "project"),
	("Work Order", "project"), ("BOM", "project"), ("BOM Creator", "project"), ("Job Card", "project"),
	("Production Plan", "project"), ("Timesheet", "parent_project"), ("Timesheet Detail", "project"),
	("Budget", "project"), ("Issue", "project"), ("Subcontracting Order", "project"),
	("Subcontracting Order Item", "project"), ("Subcontracting Receipt", "project"),
	("Subcontracting Receipt Item", "project"), ("Asset Repair", "project"), ("Asset Capitalization", "project"),
	("Installation Note", "project"), ("Request for Quotation Item", "project_name"),
]
TASK_LINK_COLUMNS = [("Timesheet Detail", "task")]


def execute():
	if not frappe.db.table_exists("Project"):
		return  # already migrated
	settings = frappe.get_doc("TaskPilot Settings")
	if not settings.enabled:
		frappe.throw("Configure and enable TaskPilot Settings before running this migration.")

	from erpnext.projects.taskpilot_client import get_client
	from erpnext.projects.taskpilot_mapping import STATUS_TO_STATE, make_identifier

	client = get_client()
	existing = {p.get("external_id"): p["identifier"] for p in client.list_projects() if p.get("external_id")}

	project_map = _push_projects(client, existing)
	task_map = _push_tasks(client, project_map)
	_remap_links(project_map, task_map)

	frappe.db.sql_ddl("DROP TABLE `tabProject`")
	frappe.db.sql_ddl("DROP TABLE `tabTask`")


def _push_projects(client, existing) -> dict:
	project_map = {}
	rows = frappe.db.sql("select name, project_name, notes, status from `tabProject`", as_dict=True)
	for row in rows:
		if row.name in existing:  # idempotent re-run
			project_map[row.name] = existing[row.name]
			continue
		created = client.create_project(
			{
				"name": row.project_name or row.name,
				"identifier": make_identifier(row.project_name or row.name),
				"external_source": "erpnext",
				"external_id": row.name,
			}
		)
		project_map[row.name] = created["identifier"]
		if row.status in ("Completed", "Cancelled"):
			client.archive_project(created["identifier"])
	return project_map


def _push_tasks(client, project_map) -> dict:
	task_map, parent_pending = {}, []
	rows = frappe.db.sql(
		"""select name, subject, description, status, priority, exp_start_date, exp_end_date,
		parent_task, project from `tabTask` where ifnull(is_template, 0) = 0 order by lft""",
		as_dict=True,
	)
	for row in rows:
		identifier = project_map.get(row.project)
		if not identifier:
			continue  # task without a (migrated) project has no TaskPilot home; logged below
		status = row.status if row.status in STATUS_TO_STATE else "Open"
		name, group = STATUS_TO_STATE[status]
		state = client.ensure_state(identifier, name, group)
		created = client.create_work_item(
			identifier,
			{
				"name": row.subject, "description_html": row.description or "<p></p>",
				"priority": {"Low": "low", "Medium": "medium", "High": "high", "Urgent": "urgent"}.get(row.priority, "none"),
				"start_date": str(row.exp_start_date) if row.exp_start_date else None,
				"target_date": str(row.exp_end_date) if row.exp_end_date else None,
				"state": state["id"], "external_source": "erpnext", "external_id": row.name,
			},
		)
		task_map[row.name] = f"{identifier}-{created['sequence_id']}"
		if row.parent_task:
			parent_pending.append((task_map[row.name], row.parent_task))
	skipped = [r.name for r in rows if r.project not in project_map]
	if skipped:
		print(f"Skipped {len(skipped)} tasks without migrated project: {skipped[:20]}")
	for child_docname, old_parent in parent_pending:
		if old_parent in task_map:
			parent_uuid = client.get_work_item(task_map[old_parent])["id"]
			client.update_work_item(child_docname, {"parent": parent_uuid})
	return task_map


def _remap_links(project_map, task_map):
	for doctype, column in PROJECT_LINK_COLUMNS:
		_remap(doctype, column, project_map)
	for doctype, column in TASK_LINK_COLUMNS:
		_remap(doctype, column, task_map)


def _remap(doctype, column, mapping):
	if not frappe.db.table_exists(doctype):
		return
	table = frappe.qb.DocType(doctype)
	for old, new in mapping.items():
		frappe.qb.update(table).set(table[column], new).where(table[column] == old).run()
	frappe.db.commit()
```

Append to `patches.txt` (after the Task 11 line): `erpnext.patches.v16_0.migrate_projects_to_taskpilot`.

- [ ] **Step 2: Dry-run procedure** (on a COPY of any real site; on the fresh dev site the tables are empty and the patch exits fast): seed two projects + three tasks (one child, one completed) via SQL inserts into the legacy tables **before** switching branches, then `bench --site tp.localhost migrate` on this branch; verify in TaskPilot UI: 2 projects (`external_id` set), 3 work items, parent link, completed one in Done; verify `select project from `tabSales Order`` shows new identifiers; re-run `bench migrate` → no duplicates (idempotency via `external_id`).
- [ ] **Step 3: Commit.** `git commit -am "feat(projects)!: one-time migration of projects/tasks to TaskPilot"`

---

### Task 13: Live smoke test + docs

**Files:**
- Create: `erpnext/projects/tests/test_taskpilot_smoke.py`
- Modify: `docs/superpowers/specs/2026-08-05-taskpilot-virtual-backend-design.md` (mark open items resolved)

- [ ] **Step 1: Smoke test** (skipped unless settings enabled — the ONLY test allowed to hit the network):

```python
import frappe
from frappe.tests import IntegrationTestCase


class TestTaskPilotSmoke(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.get_cached_doc("TaskPilot Settings").enabled:
			import unittest

			raise unittest.SkipTest("TaskPilot Settings not configured")

	def test_full_round_trip(self):
		project = frappe.get_doc({"doctype": "Project", "project_name": "Smoke Test ERPNext"}).insert()
		try:
			task = frappe.get_doc(
				{"doctype": "Task", "subject": "smoke child", "project": project.name, "status": "Working"}
			).insert()
			loaded = frappe.get_doc("Task", task.name)
			self.assertEqual(loaded.subject, "smoke child")
			self.assertEqual(loaded.status, "Working")
			loaded.status = "Completed"
			loaded.save()
			frappe.cache.delete_keys("taskpilot|")
			self.assertEqual(frappe.get_doc("Task", task.name).status, "Completed")
		finally:
			project.delete()  # archives in TaskPilot
```

Run with real credentials: `bench --site tp.localhost run-tests --app erpnext --module erpnext.projects.tests.test_taskpilot_smoke` → PASS (or SKIP without credentials).

- [ ] **Step 2: Update the spec's §7 open items** with resolutions (bench location, credentials procedure, verified frappe signatures from Task 0). Commit: `git commit -am "test(projects): live TaskPilot smoke test + spec close-out"`

---

## Self-Review Notes (kept for the executor)

- **Spec coverage:** §3.1→Task 1, §3.2→Tasks 2–3, §3.3→Tasks 4–6, §3.4→Tasks 4+7, §3.5→Task 9, §3.6→Tasks 8+10, §3.7→Task 11, §4→Task 12, §5→Tasks 2+8 (lenient mode), §6→every task + Task 13.
- **Known deliberate deviations recorded here:** `Project Type` doctype is left installed but unlinked (phase-2 decision); dashboard charts/number cards deleted rather than rewritten (rate budget); `notifications.py` Project/Task badges removed (same reason). All three are consistent with spec §3.7's "deferred" bucket even though the spec doesn't name them individually.
- **Type consistency check:** `get_client()` returns `TaskPilotClient` (Tasks 2–3 signatures used verbatim in 6–12); `STATUS_TO_STATE` consumed in Tasks 7, 9, 12; docname convention `PROJECT-seq` relied on by Tasks 7, 9, 12 (`rsplit("-", 1)`).
- **Frappe-contract risk:** Task 0 Step 2 verifies `get_list(args)`/`load_from_db` signatures against the installed framework — if they differ, adjust Tasks 6–7 before implementing them, not after.


