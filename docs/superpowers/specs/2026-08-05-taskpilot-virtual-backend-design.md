# ERPNext Projects on a TaskPilot backend — design

**Date:** 2026-08-05
**Status:** Approved design, pending implementation plan
**Repo:** erpnext fork, branch `develop` (v17 dev line, `17.0.0-dev`)
**Companion system:** TaskPilot (self-hosted Plane-family fork, source at `/mnt/projects/TaskPilot`), deployed at `taskpilot.sudiptadhara.in`

## 1. Goal

ERPNext's Projects UI/UX stays exactly as it is (desk form, list, tree, Gantt, Kanban, quick entry, Sales Order → Create Project). The data does not: **ERPNext keeps no local project database.** `Project` and `Task` become Frappe **Virtual DocTypes** whose storage is the dedicated **"ERPNext" workspace** in TaskPilot, reached over TaskPilot's REST v1 API. TaskPilot is the sole system of record for projects and tasks; ERPNext remains the system of record for money (timesheets, invoicing, GL).

Decisions made with the user:

| Decision | Choice |
|---|---|
| Storage model | Zero local tables — Virtual DocTypes, not a sync/mirror |
| Scope | All ERPNext projects live in TaskPilot; projects + tasks |
| Approach | In-place conversion of the existing `Project`/`Task` doctypes (fork), not parallel doctypes |
| Deletes | Follow TaskPilot's no-hard-delete rule: task delete → Cancelled state; project delete → archive |

## 2. Verified TaskPilot facts (from source and live schema)

Sources: `/mnt/projects/TaskPilot/apps/api/taskpilot/` and the live OpenAPI schema at `https://taskpilot-api.sudiptadhara.in/api/schema/` (66 paths; fetched 2026-08-05, copy in session scratchpad). The live schema confirms every field/endpoint claim below, plus:

- **Read-by-identifier**: `GET /workspaces/{slug}/work-items/{project_identifier}-{issue_identifier}/` — the virtual Task `load_from_db` can fetch by docname in one call, no UUID resolution on reads. Projects have no by-identifier route (detail is by UUID), so the cached identifier→UUID map is needed for projects and for all writes.
- **`external_source`/`external_id` exist on Project and State too** (not just work items) — migration idempotency and provisioned-state tagging work uniformly.
- Project schema carries `default_state`, `default_assignee`, `project_lead`, `timezone`, and an `is_time_tracking_enabled` flag — the fork has a time-tracking feature behind that flag but exposes no public worklog endpoints; Timesheets stay in ERPNext regardless (billing).
- Writable work-item fields confirmed in `IssueRequest`/`PatchedIssueRequest`: `name`, `description_html`, `priority`, `start_date`, `target_date`, `parent`, `state`, `assignees[]`, `labels[]`, `external_source`, `external_id`.
- Confirmed absent from public v1: work-item **relations** endpoints and **webhook management** (both internal-API only today) — matching the phase-2 plan to extend the fork.

- Public REST v1 (auth `X-Api-Key`, rate limit 60 req/min, 300 for service tokens):
  - Projects: `GET/POST /workspaces/<slug>/projects/`, `GET/PATCH/DELETE .../projects/<uuid>/`, archive/unarchive, summary (`api/urls/project.py`).
  - Work items: full CRUD at `.../projects/<uuid>/work-items/` (+ legacy `issues/` aliases), search, comments, links, attachments, activities (`api/urls/work_item.py`).
  - States: `GET/POST .../states/`, `GET/PATCH/DELETE .../states/<uuid>/` — custom states can be provisioned per project (`api/urls/state.py`).
  - Members, labels, cycles, modules also exposed.
- Work-item serializer (`api/serializers/issue.py:45`) excludes only `description_json`/`description_stripped` — so **`parent`, `start_date`, `target_date`, `priority`, `estimate_point`, `completed_at`, `external_source`, `external_id`** are all readable/writable. Assignees and labels writable by UUID list.
- Model (`db/models/issue.py`): `Issue.parent` (self-FK → sub-item hierarchy), `IssueRelation`/`IssueBlocker` (dependencies exist in the DB but have **no public v1 endpoint** — phase 2 adds one to the fork), M2M assignees/labels, `external_source`/`external_id` integration fields on Issue, IssueComment, IssueAttachment.
- Webhooks exist (`db/models/webhook.py`) with per-event flags (project/issue/issue_comment) — usable for cache invalidation in phase 2.
- Platform rules: no hard delete for work items (use Cancelled state; MCP/A2A cancels trigger DharaHIL human approval — REST is exempt), HTML descriptions sanitized, timestamps UTC ISO 8601.

## 3. Architecture

### 3.1 TaskPilot Settings (new Single, module Projects)

Fields: `enabled` (Check), `api_url` (Data), `workspace_slug` (Data), `api_key` (Password), `cache_ttl` (Int, default 60s), `lenient_link_validation` (Check, default on). A "Test Connection" button calls `GET /users/me/`. Secrets live here (Password field via `get_password`), not in env files.

### 3.2 REST client (`erpnext/projects/taskpilot_client.py`)

One module, one client class:

- `X-Api-Key` header; honors `X-RateLimit-Remaining`/`X-RateLimit-Reset` with sleep-and-retry backoff; request a **service token** from TaskPilot (300 req/min) for production.
- Read-through cache in Redis (`frappe.cache`) keyed by URL, TTL from settings; every write invalidates the affected project's keys.
- Identifier resolution: docnames are human identifiers (`SSSGLOBALA`, `PROJ-12`); API paths need UUIDs. The client maintains a cached identifier→UUID map from the project list and work-item search endpoints.
- Errors surface as `frappe.throw` with the TaskPilot response detail; timeouts produce a "TaskPilot unreachable" message.

### 3.3 Virtual Project

`project.json` gains `"is_virtual": 1`; table `tabProject` is dropped by the migration patch. Controller implements the virtual contract (`load_from_db`, `db_insert`, `db_update`, `delete`, static `get_list`/`get_count`) against the projects endpoints.

- **Docname** = TaskPilot project `identifier`. This string is what all cross-module `project` Link columns (GL Entry, invoices, Stock Entry, Work Order, Budget, …) store from now on.
- **Field mapping:** `project_name` ↔ `name`; `notes` ↔ `description`; `status` — Open ↔ active, Completed/Cancelled ↔ archived (+ a `status:<value>` label to distinguish, exact convention fixed in the implementation plan); `percent_complete` computed on read = completed-group work items ÷ total.
- **Financial fields compute on read.** `total_costing_amount`, `total_billable_amount`, `actual_time`, `total_purchase_cost`, `total_sales_amount`, `total_billed_amount`, `total_consumed_material_cost`, `gross_margin` were always SUMs over local tables (Timesheet Detail, Purchase Invoice Item, Sales Order, Sales Invoice, Stock Entry Detail) keyed by the project name string. `load_from_db` merges the API payload with these live SQL aggregates (reusing the existing query logic from `project.py`). Nothing is persisted.
- **Delete** = TaskPilot archive. `create_duplicate_project`, kanban helper, and `set_project_status` keep their signatures, rerouted through the API.

### 3.4 Virtual Task

`task.json` gains `"is_virtual": 1`; `is_tree` machinery (NestedSet lft/rgt) is removed — hierarchy is served by TaskPilot's `parent`.

| ERPNext field | TaskPilot | Notes |
|---|---|---|
| docname | work-item identifier (`PROJ-12`) | stable, human-readable |
| subject | `name` | |
| description | `description_html` | sanitized by TaskPilot |
| status | state (by state group) | Open→Todo (unstarted), Working→In Progress (started), Pending Review→custom `started`-group state (provisioned per project on first use), Completed→Done (completed), Cancelled→Cancelled. "Overdue" stays computed (target_date past + not completed), never stored. "Template" ceases to exist. |
| priority | `priority` | exact enum match (plus `none`) |
| exp_start_date / exp_end_date | `start_date` / `target_date` | |
| parent_task | `parent` | tree view feeds from this |
| project | owning TaskPilot project | |
| is_milestone | `milestone` label | |
| assignments (`_assign`) | `assignees` | mapped by email ↔ ERPNext User |
| — | `external_source: "erpnext"`, `external_id` | stamped on every record |

Tree view: `get_children`/`add_node` in `task.py` are rewritten to query by `parent`. Gantt/calendar: `task_calendar.js` stays; the server `get_events` path is implemented in the virtual `get_list` (date-range filters map to API query params). Kanban and list views ride on `get_list` unchanged.

### 3.5 Timesheet (stays local, unchanged schema)

Timesheet and Timesheet Detail keep their tables — TaskPilot has no time tracking and billing depends on them. Changes:

- `project`/`task` link resolution and `validate_task_project`-style checks go through the cached client instead of `frappe.db.get_value` on dropped tables.
- The stored-rollup writers die: `Task.update_time_and_costing`, `Timesheet.update_task_and_project`'s task-status writes become state transitions via the API (a completed time log still flips the work item to Done), and `Project.update_project()` disappears — totals are computed on read.
- Timesheet → Sales Invoice billing flow is untouched (it reads local timesheet rows; the SI stores the project identifier string).

### 3.6 Cross-module behavior

- The ~40 `project` Link fields keep working: Frappe link validation resolves through the virtual controller (cached). With `lenient_link_validation` on, a TaskPilot outage degrades link checks to a warning so invoicing never hard-blocks.
- Sales Order → Create Project (`selling/doctype/sales_order/mapper.py:make_project`) works through the normal document lifecycle (`insert()` → `db_insert` → API POST). The SO back-link (`link_with_sales_order`) now lives in ERPNext only as the SO's `project` field; the reverse pointer is kept in the TaskPilot project description or an `erpnext:sales_order` label (exact carrier fixed in the plan).
- GL composers and budget validation read the stored project string and keep working. Per-project **cost-center defaulting is retired**: TaskPilot has no field to carry `Project.cost_center`, so `get_default_cost_center` stops consulting the project and falls through to item/item-group/company defaults, with a fallback default cost center configurable in TaskPilot Settings. This is a deliberate functional change and is listed under Dropped (3.7).

### 3.7 Dropped and deferred

**Dropped** (no TaskPilot equivalent; removed from forms): Project Template + template tasks + `Template` task status, task weights and Task Progress / Task Weight percent-complete methods (percent-complete = completed ÷ total only), Project Update collect-progress email machinery and its scheduler jobs, holiday-aware task scheduling, per-project `cost_center` default (see 3.6 — replaced by a settings-level fallback), the `Dependent Task` orphan doctype (already dead upstream).

**Deferred to phase 2:** task dependencies (`depends_on`) via a new public relations endpoint added to the TaskPilot fork; webhook-driven cache invalidation; customer portal pages (API-backed rewrite); remaining script reports. Phase 1 rewrites only Project Summary as an API-backed report; SQL reports that join `tabProject`/`tabTask` lose those joins until then (grouping by stored identifier strings still works).

**Scheduler cleanup:** the five Projects scheduler hooks (`hourly_reminder`, `collect_project_status`, `project_status_update_reminder`, `update_project_sales_billing`, `send_project_status_email_to_users`) are removed; `set_tasks_as_overdue` is dropped (Overdue is computed on read).

## 4. Migration

One-time patch sequence (new files under `erpnext/patches/v16_0/`, appended below `[post_model_sync]`):

1. Read TaskPilot Settings; abort loudly if not configured.
2. Push every existing Project → TaskPilot project (`external_id` = old docname), every Task → work item (preserving parent chains and statuses; template tasks are skipped), building an old-name → new-identifier map.
3. Update the `project` column across all linking tables (GL Entry, SI/PI + items, SO + items, DN/PR/SE + items, Work Order, Budget, Timesheet Detail, …) and `task` in Timesheet Detail using the map.
4. Drop `tabProject` and `tabTask`.

The migration is idempotent per record via `external_id` (re-runs match instead of duplicating). Rate limits make this a queued, batched job — at 300 req/min budget the patch throttles itself.

## 5. Error handling

- TaskPilot down: Project/Task forms and lists show one clear error ("TaskPilot unreachable at <url>"); no silent empty lists.
- Transaction saves that only validate a project link: warn-and-proceed when `lenient_link_validation` is on (default), hard fail when off.
- Rate-limit exhaustion: client backs off until `X-RateLimit-Reset`; user-facing operations that would wait > a few seconds fail with a clear retry message instead of hanging.
- API validation errors (e.g. sanitized HTML, bad state) surface verbatim in the ERPNext message.

## 6. Testing

- Unit tests with a **mocked client** (fixtures captured from the live API) covering: virtual CRUD round-trips, status/state mapping both ways, identifier↔UUID resolution, percent-complete computation, financial merge-on-read, lenient vs strict link validation, delete→archive/Cancelled.
- One live smoke test (skipped unless settings configured) against a throwaway TaskPilot project: create project → create task with parent → move state → verify via API → archive.
- Migration patch gets a dry-run mode test on a seeded site.

## 7. Open items for the implementation plan

1. **Credentials:** workspace slug + a service-token API key from the user (needed for fixture capture and the smoke test).
2. **Bench/site:** where this fork runs (a local bench site is needed to develop and test; none exists in this repo checkout).
3. **Frappe virtual contract check:** verify the exact virtual-doctype hook signatures (`load_from_db`, `get_list`, link-validation path, `frappe.db.get_value` behavior for virtual doctypes) against the installed Frappe v17 at implementation start.
4. Exact carrier for the status convention and SO back-pointer (label vs description metadata) — pick during implementation of 3.3/3.6.
5. Upstream-merge policy for the fork (how often `develop` is pulled, who resolves conflicts in `projects/`).
