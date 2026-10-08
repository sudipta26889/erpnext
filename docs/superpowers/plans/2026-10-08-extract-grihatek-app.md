# Extract the TaskPilot + AI customisations into a `grihatek` Frappe app — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Production runs **unmodified upstream `frappe/erpnext`** plus a separate app, `grihatek`, that carries every customisation. Upgrading ERPNext becomes "rebuild the image", with no merge and no conflicts.

**Architecture:** The fork currently edits ERPNext in place: 48 modified files, 52 deleted, 84 added and 11 renamed (`git diff --name-status $(git merge-base develop upstream/develop) develop`). The app uses Frappe's own extension points instead:
- a doctype-level **Property Setter** makes Project and Task virtual;
- **`override_doctype_class`** swaps in the TaskPilot-backed subclasses of ERPNext's own classes;
- **`extend_doctype_class`** neutralises the project roll-ups on transactions;
- **`override_whitelisted_methods`** replaces the project link search and the SO→Project mapper;
- `fixtures` hide the fields and surfaces TaskPilot doesn't have.

The `erpnext/ai` module, the `ai/` SPA and the deploy folders move into the app unchanged.

**Tech Stack:** Frappe/ERPNext `develop` (v17-dev), Python 3.14, PostgreSQL 18 (production), MariaDB 11.8 (test bench, as upstream CI uses), frappe_docker `images/custom/Containerfile`, Vite (AI SPA).

## Global Constraints

- **The ERPNext fork ends at the cutover.** `prod-docker/apps.json` lists `https://github.com/frappe/erpnext` (branch `develop`), `grihatek` and india-compliance. Nothing in the image may patch ERPNext's files.
- **No data loss.** The tables `tabAI *`, `tabTaskPilot Settings`/Singles, `tabTimesheet*`, and every transaction's `project` value must survive the cutover byte-for-byte. The rehearsal (Task 9) proves this before production is touched.
- **No unprompted TaskPilot writes.** Upstream's scheduler jobs and roll-ups must make **zero** TaskPilot API calls unless a user changed something. The `for-ai` workspace holds the user's real projects.
- **Keep the same names.** The module stays `AI`, the doctype stays `TaskPilot Settings`, and the setting values carry over. Whitelisted API paths used by the AI SPA keep working, through redirects listed in Task 6.
- **Conventions:** whitelisted methods must have type annotations (`require_type_annotated_api_methods`); patches use `frappe.qb` only (the `postgres-compat` hook); and code follows ERPNext's style (tabs, ruff).
- **Commit trailer:** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Why this is safe enough, and the risks

| # | Risk | Likelihood | Effect if missed | Mitigation (task) |
|---|------|-----------|------------------|-------------------|
| R1 | **Failure modes change from loud to silent.** The fork *dropped* `tabProject`/`tabTask`, so any stray table access errors immediately. Stock ERPNext recreates them empty, so stray access silently returns nothing. | Certain | Wrong-but-quiet behaviour: missing totals, a check that rejects documents with a project | Every place upstream reads Project/Task straight from the database is known: 8 files, the exact ones the fork already edits (`git grep` in the *Inventory*). Each one gets a handler and a test (Tasks 4–5). A guard test re-runs the grep on each upgrade and fails if a new one appears (Task 8). |
| R2 | **Upstream scheduler jobs write to TaskPilot.** `update_project_sales_billing` (daily) re-saves every non-cancelled Project; `set_tasks_as_overdue` calls `update_status()`. | Certain without mitigation | Daily writes into the real `for-ai` projects | A save with no real field changes makes no API call, and `update_status` / the roll-up methods are no-ops. Tests run each upstream job and assert zero client writes (Task 3). |
| R3 | **Moving doctypes between apps** (`AI` module doctypes from erpnext to grihatek; `TaskPilot Settings` from module Projects to TaskPilot). Frappe's `remove_orphan_doctypes` deletes a DocType whose schema file no app ships. | Low (the app ships the files) | Tables dropped, AI history lost | The app is installed **before** the first stock-ERPNext migrate. Rehearsed on a restored production dump with row counts compared before and after (Task 9). Plus a `pg_dump` and the rollback image. |
| R4 | **Doctypes and tables the fork deleted come back.** Project Template, Project User, Project Update, five reports, web forms and portal pages are recreated with empty tables. | Certain | Clutter; a Project Template could create projects in TaskPilot | They're hidden from the workspace and sidebar, and their DocPerms are removed by a fixture (Task 6). Accepted: the tables exist but stay empty. |
| R5 | **The Property Setter applies only at runtime.** During `migrate`, `is_virtual_doctype()` reads `tabDocType.is_virtual`, which is 0, so the schema sync recreates the tables. | Certain | Empty tables (harmless) | Expected; covered by R1. Nothing at migrate time writes through the controller. |
| R6 | **Upstream ERPNext `develop` is a moving target.** It may later add code that reads Project/Task directly, or rename a method the subclasses rely on. | Medium per upgrade | A regression caught at build time, not in production | The R1 guard test plus the subclass method-signature check (Task 8) run in the image test job before any deploy. |
| R7 | **india_compliance class overrides.** Two apps can't both use `override_doctype_class` on one doctype. | None today | n/a | india_compliance only overrides `Customize Form`. The app uses `extend_doctype_class` (which stacks) for transactions, and `override_doctype_class` only for Project, Task and ToDo. |

**Verdict:** worth it, provided Task 9's rehearsal passes. The alternative is a 3–6 hour conflict merge on every upstream sync: this sync has 579 commits and 33 conflicting files, mostly in Projects, and every merge so far has carried at least one upgrade-time landmine. Rollback at every stage is: previous image tag plus `pg_restore` of the pre-cutover dump.

## Inventory (what moves where)

| Fork location | Goes to | How |
|---|---|---|
| `erpnext/ai/**` (module `AI`), `erpnext/public/ai/*`, `ai/` (SPA), `erpnext/patches/v16_0/remove_legacy_ai_page.py` | `grihatek/ai/**`, `grihatek/public/ai/*`, `frontend/ai/`, `grihatek/patches/` | move; rewrite imports `erpnext.ai.` → `grihatek.ai.` |
| `erpnext/projects/{taskpilot_client,taskpilot_mapping,todo_sync,project_financials}.py`, `doctype/taskpilot_settings/`, `projects/tests/**` | `grihatek/taskpilot/{client,mapping,todo_sync,financials}.py`, `grihatek/taskpilot/doctype/taskpilot_settings/`, `grihatek/taskpilot/tests/` | move; imports rewritten by the Task 2 script |
| Fork `Project`/`Task` controllers (`erpnext/projects/doctype/{project,task}/*.py`) | `grihatek/taskpilot/overrides/{project,task}.py` as `TaskPilotProject(erpnext Project)` / `TaskPilotTask(erpnext Task)` | port; see Task 3 |
| Fork JSON trims of Project/Task (fields removed) | `grihatek/fixtures/property_setter.json` (`hidden=1` per removed field, plus `is_virtual=1`) | generated by Task 3's script |
| SI/SO/DN `validate_proj_cust` removal; SI/PI `update_project`; Stock Entry `update_cost_in_project` | `grihatek/taskpilot/overrides/transactions.py` mixins via `extend_doctype_class` | Task 4 |
| `controllers/queries.py::get_project_name`, `sales_order/mapper.py::make_project` | `grihatek/taskpilot/overrides/whitelisted.py` via `override_whitelisted_methods` | Task 5 |
| Project cost-center lookups in `get_item_details`, PO/pick-list mappers, `email_digest` | `doc_events` on item tables setting the fallback cost center (Task 5); email digest's project section stays empty (accepted) | Task 5 |
| `profitability_analysis` company filter | the controller's `get_list` ignores filters TaskPilot can't express (Task 3); no override needed | Task 3 |
| hooks.py edits (`after_migrate` timezone heal, ToDo override + doc_events, removed portal routes/scheduler jobs) | `grihatek/hooks.py`; portal menu item disabled by patch; jobs neutralised by R2 | Tasks 1, 3, 6 |
| `normalize_deprecated_timezone`, `migrate_projects_to_taskpilot` | `grihatek/patches/` (both idempotent already) | Task 6 |
| `drop_taskpilot_replaced_doctypes` | **not carried**: it would fight stock ERPNext on every migrate | — |
| test-only fixes to upstream tests | **dropped**: stock ERPNext's tests run against stock ERPNext | — |
| `prod-docker/`, `bench-docker/`, `docs/superpowers/` | `grihatek` repo `deploy/`, `docs/` | move |

Raw-access inventory (R1). Re-run it on every upgrade:
```bash
git grep -lE 'qb\.DocType\("(Project|Task)"\)|db\.(get_value|get_values|exists|set_value|count|sql)\(\s*"(Project|Task)"|tab(Project|Task)' upstream/develop -- 'erpnext/**/*.py' ':!erpnext/projects/**' ':!**/test_*.py' ':!erpnext/patches/**'
```
Result on 2026-10-08, 8 files: `selling/doctype/sales_order/mapper.py`, `accounts/doctype/purchase_invoice/purchase_invoice.py`, `stock/get_item_details.py`, `stock/doctype/pick_list/mapper.py`, `setup/doctype/email_digest/email_digest.py`, `controllers/queries.py`, `buying/doctype/purchase_order/mapper.py`, `accounts/doctype/sales_invoice/sales_invoice.py`.

## File structure (new repo `sudipta26889/grihatek`, checked out at `/mnt/projects/grihatek`)

```
grihatek/
  pyproject.toml                # name=grihatek, [tool.bench.assets] for the AI SPA
  grihatek/
    __init__.py                 # __version__
    hooks.py                    # every integration point, one place
    modules.txt                 # AI, TaskPilot
    patches.txt
    fixtures/property_setter.json, custom_docperm.json
    ai/ ...                     # moved verbatim from erpnext/ai
    public/ai/ai.js, ai.css
    taskpilot/
      client.py mapping.py todo_sync.py financials.py
      doctype/taskpilot_settings/
      overrides/project.py task.py transactions.py whitelisted.py cost_center.py
      tests/ ...                # moved fork tests + new guard tests
    patches/ normalize_deprecated_timezone.py migrate_projects_to_taskpilot.py remove_legacy_ai_page.py disable_project_portal.py
  frontend/ai/                  # moved from erpnext repo ai/
  deploy/prod-docker/ deploy/bench-docker/
  docs/
```

---

### Task 1: Scaffold the `grihatek` app and run it next to the fork

**Files:**
- Create: `/mnt/projects/grihatek/` (via `bench new-app` inside the test container), `grihatek/hooks.py`, `grihatek/modules.txt`
- Test: `grihatek/taskpilot/tests/test_app_installed.py`

**Interfaces:**
- Produces: importable package `grihatek`; module names `AI`, `TaskPilot`; app name `grihatek` for `apps.json`.

- [ ] **Step 1: Create the app skeleton** inside a throwaway bench container built from the *current* production image (it has frappe + erpnext):
```bash
docker run -d --name gt-bench --entrypoint sleep -v /mnt/projects:/mnt/projects taskpilot-erpnext:main-d2a1052f infinity
docker exec -w /home/frappe/frappe-bench gt-bench bench new-app grihatek --no-git <<'EOF'
GrihaTEK ERP
TaskPilot-backed Projects and the Paperclip/MCP AI module for ERPNext
Sudipta Dhara
sudiptai26.889@gmail.com
gpl-3.0
y
EOF
docker cp gt-bench:/home/frappe/frappe-bench/apps/grihatek /mnt/projects/grihatek
cd /mnt/projects/grihatek && git init -b main && git add -A && git commit -m "chore: scaffold grihatek app"
```
- [ ] **Step 2: Set `modules.txt`** to exactly:
```
AI
TaskPilot
```
and delete the generated `grihatek/grihatek/` module folder (`rm -r grihatek/grihatek`). Create `grihatek/taskpilot/__init__.py` (empty).
- [ ] **Step 3: Write the failing test** `grihatek/taskpilot/tests/test_app_installed.py`:
```python
import frappe
from frappe.tests import IntegrationTestCase


class TestAppInstalled(IntegrationTestCase):
	def test_modules_belong_to_grihatek(self):
		for module in ("AI", "TaskPilot"):
			self.assertEqual(frappe.db.get_value("Module Def", module, "app_name"), "grihatek")
```
- [ ] **Step 4: Run it on a fresh test site** (MariaDB container as in the 2026-09-29 test run). Expected: FAIL, because module `AI` currently belongs to `erpnext` in the fork.
```bash
bench --site test.localhost install-app grihatek && bench --site test.localhost run-tests --module grihatek.taskpilot.tests.test_app_installed
```
- [ ] **Step 5: This test stays red until Task 2 moves the AI module.** Commit the scaffold:
```bash
git add -A && git commit -m "chore: modules AI and TaskPilot"
```

### Task 2: Move the AI module and the TaskPilot library code (pure moves)

**Files:**
- Move: `erpnext/erpnext/ai/**` → `grihatek/grihatek/ai/**`; `erpnext/erpnext/public/ai/*` → `grihatek/grihatek/public/ai/*`; `erpnext/ai/` → `grihatek/frontend/ai/`
- Move: `erpnext/erpnext/projects/{taskpilot_client,taskpilot_mapping,todo_sync,project_financials}.py` → `grihatek/grihatek/taskpilot/{client,mapping,todo_sync,financials}.py`
- Move: `erpnext/erpnext/projects/doctype/taskpilot_settings/` → `grihatek/grihatek/taskpilot/doctype/taskpilot_settings/`; set `"module": "TaskPilot"` in its JSON
- Move: `erpnext/erpnext/projects/tests/` → `grihatek/grihatek/taskpilot/tests/`
- Create: `grihatek/scripts/rewrite_imports.py`

**Interfaces:**
- Produces: `grihatek.taskpilot.client.get_client()`, `.is_enabled()`, `.is_lenient()`, `.get_fallback_cost_center()`, `.TaskPilotError`, `.TaskPilotClient`; `grihatek.taskpilot.mapping.tp_to_project/tp_to_task/...`; `grihatek.ai.*` (same names as before, new package path).

- [ ] **Step 1: Copy the files** (they're copies until the cutover; the fork keeps its originals):
```bash
F=/mnt/projects/erpnext; G=/mnt/projects/grihatek/grihatek
cp -r $F/erpnext/ai $G/ai; mkdir -p $G/public && cp -r $F/erpnext/public/ai $G/public/ai
mkdir -p /mnt/projects/grihatek/frontend && cp -r $F/ai /mnt/projects/grihatek/frontend/ai
cp $F/erpnext/projects/taskpilot_client.py $G/taskpilot/client.py
cp $F/erpnext/projects/taskpilot_mapping.py $G/taskpilot/mapping.py
cp $F/erpnext/projects/todo_sync.py $G/taskpilot/todo_sync.py
cp $F/erpnext/projects/project_financials.py $G/taskpilot/financials.py
mkdir -p $G/taskpilot/doctype && cp -r $F/erpnext/projects/doctype/taskpilot_settings $G/taskpilot/doctype/
cp -r $F/erpnext/projects/tests $G/taskpilot/tests
touch $G/taskpilot/doctype/__init__.py
```
- [ ] **Step 2: Write `grihatek/scripts/rewrite_imports.py`**, the one mechanical rename shared by every moved file:
```python
"""Rewrite fork import paths to their grihatek locations. Idempotent."""

import pathlib
import re
import sys

MAP = {
	r"\berpnext\.ai\b": "grihatek.ai",
	r"\berpnext\.projects\.taskpilot_client\b": "grihatek.taskpilot.client",
	r"\berpnext\.projects\.taskpilot_mapping\b": "grihatek.taskpilot.mapping",
	r"\berpnext\.projects\.todo_sync\b": "grihatek.taskpilot.todo_sync",
	r"\berpnext\.projects\.project_financials\b": "grihatek.taskpilot.financials",
	r"\berpnext\.projects\.tests\b": "grihatek.taskpilot.tests",
	r"\berpnext\.projects\.doctype\.taskpilot_settings\b": "grihatek.taskpilot.doctype.taskpilot_settings",
	r"/assets/erpnext/ai/": "/assets/grihatek/ai/",
}


def main(root: str) -> int:
	changed = 0
	for path in pathlib.Path(root).rglob("*"):
		if path.suffix not in {".py", ".js", ".ts", ".tsx", ".json", ".html"} or "node_modules" in path.parts:
			continue
		text = path.read_text()
		new = text
		for pattern, repl in MAP.items():
			new = re.sub(pattern, repl, new)
		if new != text:
			path.write_text(new)
			changed += 1
	print(f"rewrote {changed} files")
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1]))
```
- [ ] **Step 3: Run it, then confirm nothing still points at the old paths:**
```bash
cd /mnt/projects/grihatek && python3 scripts/rewrite_imports.py grihatek && python3 scripts/rewrite_imports.py frontend
grep -rnE 'erpnext\.(ai|projects\.(taskpilot_|todo_sync|project_financials|tests))' grihatek frontend --include='*.py' --include='*.js' --include='*.ts' --include='*.json' ; echo "exit=$? (1 means clean)"
```
Expected: `exit=1 (1 means clean)`.
- [ ] **Step 4: Set the TaskPilot Settings module:** in `grihatek/taskpilot/doctype/taskpilot_settings/taskpilot_settings.json` change `"module": "Projects"` to `"module": "TaskPilot"`.
- [ ] **Step 5: Run the moved library tests plus Task 1's test** on a test site that has the fork's `erpnext` (the ported controllers come in Task 3, so for now run only the modules that don't touch Project/Task):
```bash
for m in test_app_installed test_taskpilot_client test_taskpilot_mapping test_migration_patch test_todo_sync; do bench --site test.localhost run-tests --module grihatek.taskpilot.tests.$m; done
bench --site test.localhost run-tests --app grihatek --module grihatek.ai.tests
```
Expected: all pass. `test_app_installed` stays red until the cutover rehearsal, because the fork still owns module `AI`. Mark it with `@unittest.skipUnless(frappe.db.get_value("Module Def", "AI", "app_name") == "grihatek", "pre-cutover")` and note that in the commit.
- [ ] **Step 6: Commit:** `git add -A && git commit -m "feat: move AI module and TaskPilot library into grihatek"`

### Task 3: TaskPilot-backed Project and Task as subclasses of ERPNext's classes

**Files:**
- Create: `grihatek/taskpilot/overrides/__init__.py`, `overrides/project.py`, `overrides/task.py`
- Create: `grihatek/fixtures/property_setter.json` (generated in Step 1)
- Create: `grihatek/scripts/gen_property_setters.py`
- Modify: `grihatek/hooks.py`
- Test: `grihatek/taskpilot/tests/test_virtual_project.py`, `test_virtual_task.py` (moved in Task 2; imports point at the overrides), new `test_no_unprompted_writes.py`

**Interfaces:**
- Consumes: `grihatek.taskpilot.client.get_client/is_enabled/is_lenient`, `grihatek.taskpilot.mapping.*` (Task 2)
- Produces: `grihatek.taskpilot.overrides.project.TaskPilotProject` (subclass of `erpnext.projects.doctype.project.project.Project`) and `grihatek.taskpilot.overrides.task.TaskPilotTask` (subclass of `erpnext.projects.doctype.task.task.Task`); both with `load_from_db`, `db_insert`, `db_update`, `delete`, static `get_list/get_count/get_stats`, ported from the fork's `erpnext/projects/doctype/{project,task}/{project,task}.py` (fork commit `790f122f89`).

- [ ] **Step 1: Generate the Property Setters.** Write `grihatek/scripts/gen_property_setters.py`:
```python
"""Emit property_setter.json: is_virtual=1 on Project/Task, hidden=1 on every field the fork removed.

Run from the erpnext fork checkout: python3 gen_property_setters.py <upstream_ref> <fork_ref> > out.json
"""

import json
import subprocess
import sys


def fields(ref: str, doctype: str) -> set[str]:
	path = f"erpnext/projects/doctype/{doctype}/{doctype}.json"
	data = json.loads(subprocess.check_output(["git", "show", f"{ref}:{path}"]))
	return {f["fieldname"] for f in data["fields"]}


def setter(doctype, prop, value, ptype, field=None):
	return {
		"doctype": "Property Setter",
		"doctype_or_field": "DocField" if field else "DocType",
		"doc_type": doctype,
		"field_name": field,
		"property": prop,
		"property_type": ptype,
		"value": value,
		"module": "TaskPilot",
		"is_system_generated": 0,
	}


def main(upstream_ref: str, fork_ref: str) -> None:
	out = []
	for doctype, label in (("project", "Project"), ("task", "Task")):
		out.append(setter(label, "is_virtual", "1", "Check"))
		for field in sorted(fields(upstream_ref, doctype) - fields(fork_ref, doctype)):
			out.append(setter(label, "hidden", "1", "Check", field))
	print(json.dumps(out, indent=1))


if __name__ == "__main__":
	main(sys.argv[1], sys.argv[2])
```
Run it: `cd /mnt/projects/erpnext && python3 /mnt/projects/grihatek/scripts/gen_property_setters.py upstream/develop 790f122f89 > /mnt/projects/grihatek/grihatek/fixtures/property_setter.json`.
- [ ] **Step 2: Wire the hooks.** Append to `grihatek/hooks.py`:
```python
fixtures = [
	{"dt": "Property Setter", "filters": [["module", "=", "TaskPilot"]]},
	{"dt": "Custom DocPerm", "filters": [["parent", "in", ["Project Template", "Project Update"]]]},
]

override_doctype_class = {
	"Project": "grihatek.taskpilot.overrides.project.TaskPilotProject",
	"Task": "grihatek.taskpilot.overrides.task.TaskPilotTask",
	"ToDo": "grihatek.taskpilot.todo_sync.CustomToDo",
}

doc_events = {
	"ToDo": {
		"on_update": "grihatek.taskpilot.todo_sync.push_assignees",
		"on_trash": "grihatek.taskpilot.todo_sync.push_assignees",
	},
}

after_migrate = ["grihatek.patches.normalize_deprecated_timezone.execute"]
```
- [ ] **Step 3: Write the failing tests for R2**, `grihatek/taskpilot/tests/test_no_unprompted_writes.py`:
```python
from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from grihatek.taskpilot.tests import fixtures

WRITES = ("create_project", "update_project", "archive_project", "create_work_item", "update_work_item")


def _client():
	from unittest.mock import MagicMock

	c = MagicMock()
	c.list_projects.return_value = [fixtures.PROJECT]
	c.get_project.return_value = fixtures.PROJECT
	c.list_work_items.return_value = [fixtures.WORK_ITEM]
	c.get_work_item.return_value = fixtures.WORK_ITEM
	c.states.return_value = fixtures.STATES
	return c


@patch("grihatek.taskpilot.overrides.project.is_enabled", return_value=True)
@patch("grihatek.taskpilot.overrides.task.is_enabled", return_value=True)
class TestNoUnpromptedWrites(IntegrationTestCase):
	def assert_no_writes(self, client):
		for name in WRITES:
			self.assertFalse(getattr(client, name).called, f"{name} was called")

	def test_daily_update_project_sales_billing_writes_nothing(self, *_):
		from erpnext.projects.doctype.project.project import update_project_sales_billing

		c = _client()
		with patch("grihatek.taskpilot.overrides.project.get_client", return_value=c):
			update_project_sales_billing()
		self.assert_no_writes(c)

	def test_daily_set_tasks_as_overdue_writes_nothing(self, *_):
		from erpnext.projects.doctype.task.task import set_tasks_as_overdue

		c = _client()
		with patch("grihatek.taskpilot.overrides.task.get_client", return_value=c):
			set_tasks_as_overdue()
		self.assert_no_writes(c)

	def test_unchanged_save_writes_nothing(self, *_):
		import frappe

		c = _client()
		with patch("grihatek.taskpilot.overrides.project.get_client", return_value=c):
			frappe.get_doc("Project", "WEBSITE").save()
		self.assert_no_writes(c)

	def test_changed_save_writes_once(self, *_):
		import frappe

		c = _client()
		with patch("grihatek.taskpilot.overrides.project.get_client", return_value=c):
			doc = frappe.get_doc("Project", "WEBSITE")
			doc.project_name = "Website Revamp 2"
			doc.save()
		self.assertEqual(c.update_project.call_count, 1)

	def test_collect_progress_filters_return_empty_not_error(self, *_):
		import frappe

		c = _client()
		with patch("grihatek.taskpilot.overrides.project.get_client", return_value=c):
			rows = frappe.get_all("Project", filters={"collect_progress": 1, "frequency": "Daily", "status": "Open"})
		self.assertEqual(rows, [])
```
- [ ] **Step 4: Run them.** Expected: FAIL with `ModuleNotFoundError: grihatek.taskpilot.overrides.project`.
- [ ] **Step 5: Port the controllers.** Create `overrides/project.py` by copying the fork's `erpnext/projects/doctype/project/project.py` (at `790f122f89`), then:
  1. change `class Project(Document):` to:
```python
from erpnext.projects.doctype.project.project import Project as ERPNextProject


class TaskPilotProject(ERPNextProject):
```
  2. keep every method the fork defines (`load_from_db`, `db_insert`, `db_update`, `delete`, `get_list`, `get_count`, `get_stats`, `onload`, `validate`, …) unchanged;
  3. add the block below, which neutralises upstream methods that would otherwise run against the empty local table or push roll-ups into TaskPilot:
```python
	# ponytail: upstream roll-ups and project-template machinery have no TaskPilot equivalent;
	# totals are computed on read (grihatek.taskpilot.financials). Each is reachable from stock
	# ERPNext code paths (transactions, scheduler), so each must stay a no-op.
	def update_costing(self): ...
	def calculate_gross_margin(self): ...
	def update_purchase_costing(self): ...
	def update_sales_amount(self): ...
	def update_billed_amount(self): ...
	def set_consumed_material_cost(self): ...
	def update_project(self): ...
	def update_percent_complete(self): ...
	def copy_from_template(self): ...
	def send_welcome_email(self): ...
	def link_with_sales_order(self): ...
	def after_insert(self): ...
	def on_trash(self): ...
	def after_rename(self, *args, **kwargs): ...
```
  4. make `db_update` skip the API call when no mapped field changed. Snapshot the mapped payload in `load_from_db` (`self._tp_snapshot = project_to_tp(self)`), compare it in `db_update`, and return early if they're equal. `project_to_tp` already exists in `grihatek.taskpilot.mapping`;
  5. in `get_list`, return `[]` when a filter names a field that `mapping` doesn't map. Don't raise: upstream's `get_projects_for_collect_progress` filters on `collect_progress`/`frequency`.
  
  Do the same for `overrides/task.py`: `class TaskPilotTask(erpnext.projects.doctype.task.task.Task)`, with no-ops for `update_nsm_model`, `update_time_and_costing`, `update_project`, `update_previous_project`, `check_recursion`, `reschedule_dependent_tasks`, `populate_depends_on`, `remove_from_previous_parent_depends_on`, `remove_from_parent_depends_on`, `update_depends_on`, `unassign_todo`, `update_status`, `on_trash`, `after_delete`, plus the same snapshot rule in `db_update`.
- [ ] **Step 6: Point the moved tests at the overrides.** In `test_virtual_project.py` / `test_virtual_task.py`, replace the patch targets `erpnext.projects.doctype.project.project.` → `grihatek.taskpilot.overrides.project.` and `erpnext.projects.doctype.task.task.` → `grihatek.taskpilot.overrides.task.`.
- [ ] **Step 7: Run all TaskPilot tests on a site with STOCK erpnext + grihatek** (from here on, the test image is built from `apps.json` with `frappe/erpnext` develop + grihatek):
```bash
bench --site test.localhost migrate && bench --site test.localhost run-tests --app grihatek
```
Expected: all pass, including the 5 new ones.
- [ ] **Step 8: Commit:** `git commit -am "feat: TaskPilot Project/Task as overrides of the stock ERPNext classes"`

### Task 4: Neutralise project roll-ups and customer checks on transactions

**Files:**
- Create: `grihatek/taskpilot/overrides/transactions.py`
- Modify: `grihatek/hooks.py`
- Test: `grihatek/taskpilot/tests/test_transactions.py`

**Interfaces:**
- Produces: mixins `ProjectCustomerCheck`, `NoProjectRollup`, `NoStockEntryProjectCost`, registered through `extend_doctype_class` (stacks with other apps).

- [ ] **Step 1: Write the failing test** `grihatek/taskpilot/tests/test_transactions.py`:
```python
from unittest.mock import patch

from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.tests.utils import ERPNextTestSuite

from grihatek.taskpilot.tests import fixtures


@patch("grihatek.taskpilot.overrides.project.is_enabled", return_value=True)
class TestTransactionsWithTaskPilotProject(ERPNextTestSuite):
	def setUp(self):
		self.client = patch("grihatek.taskpilot.overrides.project.get_client").start()
		self.client.return_value.get_project.return_value = fixtures.PROJECT
		self.client.return_value.list_projects.return_value = [fixtures.PROJECT]
		self.addCleanup(patch.stopall)

	def test_sales_order_with_project_saves(self, _):
		so = make_sales_order(do_not_save=True)
		so.project = "WEBSITE"
		so.insert()  # upstream validate_proj_cust would throw: tabProject is empty

	def test_sales_invoice_submit_does_not_touch_taskpilot(self, _):
		si = create_sales_invoice(do_not_save=True)
		si.project = "WEBSITE"
		si.insert()
		si.submit()
		self.assertFalse(self.client.return_value.update_project.called)
```
- [ ] **Step 2: Run it.** Expected: FAIL with `Customer _Test Customer does not belong to project WEBSITE`.
- [ ] **Step 3: Implement** `grihatek/taskpilot/overrides/transactions.py`:
```python
"""Transaction-side halves of the Project cutover. Registered via extend_doctype_class.

Upstream checks "customer belongs to project" and pushes billed/purchase/consumed totals into the
Project row. TaskPilot projects carry no customer and no totals (totals are computed on read by
grihatek.taskpilot.financials), so the check is dropped and the roll-ups do nothing.
"""


class ProjectCustomerCheck:
	def validate_proj_cust(self):
		pass


class NoProjectRollup:
	def update_project(self):
		pass


class NoStockEntryProjectCost:
	def update_cost_in_project(self):
		pass
```
and in `hooks.py`:
```python
extend_doctype_class = {
	"Sales Order": ["grihatek.taskpilot.overrides.transactions.ProjectCustomerCheck"],
	"Delivery Note": ["grihatek.taskpilot.overrides.transactions.ProjectCustomerCheck"],
	"Sales Invoice": [
		"grihatek.taskpilot.overrides.transactions.ProjectCustomerCheck",
		"grihatek.taskpilot.overrides.transactions.NoProjectRollup",
	],
	"Purchase Invoice": ["grihatek.taskpilot.overrides.transactions.NoProjectRollup"],
	"Stock Entry": ["grihatek.taskpilot.overrides.transactions.NoStockEntryProjectCost"],
}
```
- [ ] **Step 4: Run the test, then upstream's own suites for these doctypes**, which must not regress:
```bash
bench --site test.localhost run-tests --module grihatek.taskpilot.tests.test_transactions
for m in erpnext.selling.doctype.sales_order.test_sales_order erpnext.accounts.doctype.sales_invoice.test_sales_invoice erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice erpnext.stock.doctype.delivery_note.test_delivery_note erpnext.stock.doctype.stock_entry.test_stock_entry; do bench --site test.localhost run-tests --module $m; done
```
Expected: the grihatek test passes. Upstream suites pass except their own Project-dependent tests (record the names; they test behaviour TaskPilot intentionally drops).
- [ ] **Step 5: Commit:** `git commit -am "feat: drop project customer check and roll-ups on transactions"`

### Task 5: Link search, SO→Project mapper, fallback cost center

**Files:**
- Create: `grihatek/taskpilot/overrides/whitelisted.py`, `overrides/cost_center.py`
- Modify: `grihatek/hooks.py`
- Test: `grihatek/taskpilot/tests/test_whitelisted.py`

**Interfaces:**
- Produces: `get_project_name(doctype, txt, searchfield, start, page_len, filters) -> list[list[str]]`, `make_project(source_name, target_doc=None) -> Document`, `set_fallback_cost_center(doc, method=None) -> None`.

- [ ] **Step 1: Write the failing tests** `grihatek/taskpilot/tests/test_whitelisted.py`:
```python
from unittest.mock import patch

import frappe
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.tests.utils import ERPNextTestSuite

from grihatek.taskpilot.tests import fixtures


class TestWhitelisted(ERPNextTestSuite):
	@patch("grihatek.taskpilot.overrides.whitelisted.get_client")
	def test_project_link_search_goes_to_taskpilot(self, get_client):
		get_client.return_value.list_projects.return_value = [fixtures.PROJECT]
		fn = frappe.get_attr(frappe.get_hooks("override_whitelisted_methods")["erpnext.controllers.queries.get_project_name"][-1])
		self.assertEqual(fn("Project", "revamp", "name", 0, 20, None), [["WEBSITE", "Website Revamp"]])
		self.assertEqual(fn("Project", "nomatch", "name", 0, 20, None), [])

	def test_so_to_project_carries_external_id(self):
		so = make_sales_order()
		fn = frappe.get_attr(frappe.get_hooks("override_whitelisted_methods")["erpnext.selling.doctype.sales_order.mapper.make_project"][-1])
		self.assertEqual(fn(so.name).external_id, so.name)

	@patch("grihatek.taskpilot.overrides.cost_center.get_fallback_cost_center", return_value="Main - _TC")
	def test_item_rows_with_project_get_fallback_cost_center(self, _):
		so = make_sales_order(do_not_save=True)
		for row in so.items:
			row.project, row.cost_center = "WEBSITE", None
		from grihatek.taskpilot.overrides.cost_center import set_fallback_cost_center

		set_fallback_cost_center(so)
		self.assertTrue(all(r.cost_center == "Main - _TC" for r in so.items))
```
- [ ] **Step 2: Run them.** Expected: FAIL with `KeyError: 'erpnext.controllers.queries.get_project_name'`.
- [ ] **Step 3: Implement** `overrides/whitelisted.py`. The bodies are the fork's own code from `erpnext/controllers/queries.py` (the `get_project_name` HEAD hunk) and `selling/doctype/sales_order/mapper.py::make_project`:
```python
import frappe
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc

from grihatek.taskpilot.client import TaskPilotError, get_client
from grihatek.taskpilot.mapping import tp_to_project


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_project_name(doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict | None):
	# filters like customer/company are ignored: TaskPilot projects don't carry them
	try:
		rows = [tp_to_project(p) for p in get_client().list_projects()]
	except TaskPilotError:
		return []
	needle = (txt or "").lower()
	rows = [r for r in rows if needle in r.name.lower() or needle in (r.project_name or "").lower()]
	return [[r.name, r.project_name] for r in rows[int(start) : int(start) + int(page_len)]]


@frappe.whitelist()
def make_project(source_name: str, target_doc: str | dict | Document | None = None):
	def set_missing_values(source, target):
		# the reverse SO pointer rides TaskPilot's external_id
		target.external_id = source.name

	return get_mapped_doc(
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
```
`overrides/cost_center.py`:
```python
from grihatek.taskpilot.client import get_fallback_cost_center


def set_fallback_cost_center(doc, method=None):
	"""Upstream reads Project.cost_center from the (empty) local table; use the TaskPilot fallback."""
	fallback = None
	for row in doc.get("items") or []:
		if row.get("project") and not row.get("cost_center"):
			fallback = fallback or get_fallback_cost_center()
			row.cost_center = fallback
```
`hooks.py`:
```python
override_whitelisted_methods = {
	"erpnext.controllers.queries.get_project_name": "grihatek.taskpilot.overrides.whitelisted.get_project_name",
	"erpnext.selling.doctype.sales_order.mapper.make_project": "grihatek.taskpilot.overrides.whitelisted.make_project",
}
```
and add `"validate": "grihatek.taskpilot.overrides.cost_center.set_fallback_cost_center"` under `doc_events` for `Sales Order`, `Sales Invoice`, `Purchase Order`, `Purchase Invoice`, `Delivery Note` and `Pick List`.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -am "feat: TaskPilot link search, SO->Project mapper, fallback cost center"`

### Task 6: Patches, hidden surfaces, portal

**Files:**
- Move: fork `erpnext/patches/v16_0/{normalize_deprecated_timezone,migrate_projects_to_taskpilot,remove_legacy_ai_page}.py` → `grihatek/patches/`
- Create: `grihatek/patches/disable_project_portal.py`, `grihatek/patches.txt`
- Create: workspace and sidebar for module `TaskPilot` (copy the fork's `erpnext/projects/workspace/projects/projects.json` and `erpnext/projects/sidebar/projects/projects.json` into `grihatek/taskpilot/workspace/…` and `…/sidebar/…`, renamed `TaskPilot Projects`)
- Test: `grihatek/taskpilot/tests/test_patches.py`

- [ ] **Step 1: Write the failing test:**
```python
import frappe
from frappe.tests import IntegrationTestCase


class TestPatches(IntegrationTestCase):
	def test_portal_projects_menu_disabled(self):
		from grihatek.patches.disable_project_portal import execute

		execute()
		row = frappe.db.get_value("Portal Menu Item", {"route": "/project"}, "enabled")
		self.assertIn(row, (0, None))

	def test_replaced_doctypes_are_read_only_for_system_manager_only(self):
		# any Custom DocPerm row replaces the shipped perms; the fixture ships exactly one per doctype
		for dt in ("Project Template", "Project Update"):
			rows = frappe.get_all("Custom DocPerm", filters={"parent": dt}, fields=["role", "read", "write", "create"])
			self.assertEqual(rows, [{"role": "System Manager", "read": 1, "write": 0, "create": 0}])
```
Create `grihatek/fixtures/custom_docperm.json`. It's loaded by the `fixtures` hook from Task 3 Step 2:
```json
[
 {"doctype": "Custom DocPerm", "parent": "Project Template", "parenttype": "DocType", "parentfield": "permissions",
  "role": "System Manager", "permlevel": 0, "read": 1, "write": 0, "create": 0, "delete": 0},
 {"doctype": "Custom DocPerm", "parent": "Project Update", "parenttype": "DocType", "parentfield": "permissions",
  "role": "System Manager", "permlevel": 0, "read": 1, "write": 0, "create": 0, "delete": 0}
]
```
- [ ] **Step 2: Run it.** Expected: FAIL with `ModuleNotFoundError: grihatek.patches.disable_project_portal`.
- [ ] **Step 3: Implement** `grihatek/patches/disable_project_portal.py`:
```python
import frappe


def execute():
	portal = frappe.qb.DocType("Portal Menu Item")
	frappe.qb.update(portal).set(portal.enabled, 0).where(portal.route == "/project").run()
```
`grihatek/patches.txt`:
```
[pre_model_sync]

[post_model_sync]
grihatek.patches.normalize_deprecated_timezone
grihatek.patches.migrate_projects_to_taskpilot
grihatek.patches.remove_legacy_ai_page
grihatek.patches.disable_project_portal
```
Rewrite their imports with `scripts/rewrite_imports.py`. All four are idempotent; on production the first two find nothing to do.
- [ ] **Step 4: Run the test plus `bench --site test.localhost migrate` twice.** Expected: PASS, and the second migrate is a no-op.
- [ ] **Step 5: Commit:** `git commit -am "feat: carry patches, hide replaced surfaces, disable project portal"`

### Task 7: AI SPA build and asset paths

**Files:**
- Modify: `grihatek/pyproject.toml` (add `[tool.bench.assets]` for `frontend/ai`, copied from the fork's `pyproject.toml` banking entry pattern), `package.json` (`"build": "cd frontend/ai && yarn install && yarn build"`)
- Modify: the AI page's boot shell (moved in Task 2) so the bundle URL is `/assets/grihatek/ai/…`
- Test: `frontend/ai/test/*` (moved), plus a page-load check

- [ ] **Step 1:** Build the SPA: `cd /mnt/projects/grihatek && yarn build`. Expected: `grihatek/public/ai/` gets the hashed bundle.
- [ ] **Step 2:** Run the SPA's tests: `cd frontend/ai && yarn test`. Expected: PASS.
- [ ] **Step 3:** On the test site, `curl -s -o /dev/null -w '%{http_code}' -b <login cookie> http://127.0.0.1:8000/app/ai`. Expected: `200`; the HTML references `/assets/grihatek/ai/`.
- [ ] **Step 4: Commit:** `git commit -am "build: AI SPA builds from grihatek"`

### Task 8: Upgrade guard tests (the reason this whole plan exists)

**Files:**
- Create: `grihatek/taskpilot/tests/test_upgrade_guards.py`

- [ ] **Step 1: Write the tests**, which fail the image build if upstream drifts:
```python
import inspect
import pathlib
import re

import erpnext
from erpnext.projects.doctype.project.project import Project
from erpnext.projects.doctype.task.task import Task
from frappe.tests import IntegrationTestCase

from grihatek.taskpilot.overrides.project import TaskPilotProject
from grihatek.taskpilot.overrides.task import TaskPilotTask

RAW = re.compile(
	r'qb\.DocType\("(Project|Task)"\)|db\.(get_value|get_values|exists|set_value|count|sql)\(\s*"(Project|Task)"|tab(Project|Task)'
)
KNOWN = {
	"selling/doctype/sales_order/mapper.py",
	"accounts/doctype/purchase_invoice/purchase_invoice.py",
	"stock/get_item_details.py",
	"stock/doctype/pick_list/mapper.py",
	"setup/doctype/email_digest/email_digest.py",
	"controllers/queries.py",
	"buying/doctype/purchase_order/mapper.py",
	"accounts/doctype/sales_invoice/sales_invoice.py",
}


class TestUpgradeGuards(IntegrationTestCase):
	def test_no_new_raw_project_task_access_upstream(self):
		root = pathlib.Path(erpnext.__file__).parent
		hits = {
			str(p.relative_to(root))
			for p in root.rglob("*.py")
			if "projects" not in p.parts and "patches" not in p.parts and not p.name.startswith("test_")
			and RAW.search(p.read_text())
		}
		self.assertEqual(hits - KNOWN, set(), "upstream added raw Project/Task access: add a grihatek handler + test, then KNOWN")

	def test_every_upstream_project_method_is_reviewed(self):
		def own(cls):
			return {n for n, v in vars(cls).items() if inspect.isfunction(v)}

		for upstream, ours in ((Project, TaskPilotProject), (Task, TaskPilotTask)):
			unreviewed = own(upstream) - own(ours) - {"onload", "before_print", "get_customer_details", "is_overdue", "has_webform_permission"}
			self.assertEqual(unreviewed, set(), f"new upstream {upstream.__name__} methods: decide no-op or keep, then list here")
```
- [ ] **Step 2: Run them.** Expected: PASS on today's upstream. Then add a dummy `frappe.db.get_value("Project", …)` line to any stock ERPNext file in the test container and re-run. Expected: the first test FAILS naming that file. Revert the dummy line.
- [ ] **Step 3: Commit:** `git commit -am "test: upgrade guards for raw Project/Task access and new upstream methods"`

### Task 9: Rehearse the cutover on a copy of production (go/no-go gate)

**Files:**
- Create: `deploy/rehearse-cutover.sh` (in grihatek)
- Output: `docs/cutover-rehearsal-YYYY-MM-DD.md` with the numbers below

- [ ] **Step 1: Restore production into a staging database** on the host Postgres:
```bash
pg_dump -h 127.0.0.1 -U erpnext_db_user -Fc -f /mnt/projects/grihatek/deploy/backups/prod-$(date +%F).dump erpnext_db
createdb -h 127.0.0.1 -U postgres -O erpnext_db_user erpnext_staging
pg_restore -h 127.0.0.1 -U postgres -d erpnext_staging --no-owner --role=erpnext_db_user /mnt/projects/grihatek/deploy/backups/prod-$(date +%F).dump
```
- [ ] **Step 2: Record before-counts** of what must survive:
```sql
SELECT 'ai_tables', count(*) FROM information_schema.tables WHERE table_name LIKE 'tabAI%';
SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE relname LIKE 'tabAI%' OR relname IN ('tabTimesheet','tabTimesheet Detail','tabSingles') ORDER BY 1;
SELECT count(*) FROM "tabSales Invoice" WHERE project IS NOT NULL AND project <> '';
SELECT field, value FROM "tabSingles" WHERE doctype IN ('TaskPilot Settings','AI Settings') ORDER BY 1;
```
- [ ] **Step 3: Run a staging stack** from the new image (`apps.json` = stock erpnext develop + grihatek + india-compliance). Point a copy of `prod-docker/compose.yaml` at DB `erpnext_staging`, use a different `HTTP_PUBLISH_PORT` and Redis DB numbers, and set TaskPilot Settings' workspace to the empty `erpnext` workspace, so a mistake can't write to `for-ai`. Then:
```bash
# R3: hand module AI to grihatek first, so neither install-app nor the orphan reaper sees it as erpnext's
psql -h 127.0.0.1 -U erpnext_db_user -d erpnext_staging -c "UPDATE \"tabModule Def\" SET app_name='grihatek' WHERE name='AI'"
bench --site erp.localhost install-app grihatek   # BEFORE the first stock-erpnext migrate
bench --site erp.localhost migrate
```
- [ ] **Step 4: Compare after-counts** with Step 2's queries. **Go criteria:** every `tabAI*` row count identical; Singles for both settings identical; Sales Invoice project count identical; `Module Def` AI and TaskPilot both `app_name = grihatek`; the migrate log shows no `Orphaned DocType(s) found` entry naming an AI or TaskPilot doctype.
- [ ] **Step 5: Smoke-test staging** with the production checks from 2026-09-29: ping 200; login 200; MCP `initialize`/`tools/list` (15 tools)/`get_company_context`; Project list and Task list return the workspace's projects; open, edit and save one Project (exactly 1 TaskPilot write); run `bench --site erp.localhost execute erpnext.projects.doctype.project.project.update_project_sales_billing` (0 writes, checked in TaskPilot's activity log).
- [ ] **Step 6: Write `docs/cutover-rehearsal-YYYY-MM-DD.md`** with the numbers, then tear staging down (`dropdb erpnext_staging`, remove the containers). **No-go on any mismatch:** fix the cause in Tasks 2–6 and rehearse again.

### Task 10: Production cutover

- [ ] **Step 1:** Announce a short maintenance window. Take the pre-cutover `pg_dump` (as on 2026-09-29) and keep the current image tag as the rollback.
- [ ] **Step 2:** Run `UPDATE "tabModule Def" SET app_name='grihatek' WHERE name='AI'` on `erpnext_db`. Then set `CUSTOM_TAG` to the new image in `prod-docker/.env`, and run `docker compose up -d`, `bench --site erp.localhost install-app grihatek` and `bench --site erp.localhost migrate`, in exactly that order (the order Task 9 rehearsed). Restart the services.
- [ ] **Step 3:** Repeat Task 9 Steps 2, 4 and 5 against production, using the real `for-ai` workspace but no edit/save test there.
- [ ] **Step 4 — Rollback if any check fails:** set `CUSTOM_TAG` back, `docker compose up -d`, then `pg_restore --clean -d erpnext_db <pre-cutover dump>`.
- [ ] **Step 5:** Archive the fork. Tag `sudipta26889/erpnext` `archive/fork-final`, and update the README to point at `grihatek`. From now on an upgrade is: rebuild the image, test job (`run-tests --app grihatek` + the Task 8 guards), deploy.

---

## Upgrade procedure after cutover (what "git merge upstream" becomes)

1. Rebuild the image: frappe_docker with `--secret=id=apps_json,src=deploy/prod-docker/apps.json --no-cache-filter=builder`. ERPNext comes straight from `frappe/erpnext` develop.
2. Run `bench run-tests --app grihatek` on a throwaway MariaDB site. The guard tests (Task 8) tell you exactly what upstream changed that matters.
3. Back up the database, deploy, migrate and smoke-test, using the same steps as Task 10.
