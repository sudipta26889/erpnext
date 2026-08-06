# ERPNext AI Cofounder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `AI` entry to the ERPNext desk icon rail between Home and Invoicing, opening a conversation with the Paperclip CEO agent, which can read and act on ERPNext through a governed MCP tool server.

**Architecture:** ERPNext exposes itself as an MCP server — one whitelisted JSON-RPC endpoint inside Frappe, so `frappe.session.user` is correct and Frappe enforces every permission natively. Paperclip registers it as an `mcp_remote` tool connection and gates writes through its tool gateway. The desk tab is a React SPA mounted in a Frappe desk Page that talks to a standing Paperclip Issue via comments, polling run events for live progress.

**Tech Stack:** Python 3.10+ / Frappe v17 (`frappe.qb`, type-annotated whitelisted methods), React 19 + Vite + Tailwind 4 + `frappe-react-sdk`, MCP JSON-RPC over HTTP POST.

**Spec:** `docs/superpowers/specs/2026-08-06-erpnext-ai-cofounder-design.md`
**Branch:** `feature/erpnext-ai-cofounder` (already checked out)

## Global Constraints

- **Type-annotated whitelisted methods are mandatory.** `require_type_annotated_api_methods = True` in `hooks.py`. Every `@frappe.whitelist()` function needs full parameter and return annotations or it raises at import.
- **No raw SQL.** A `postgres-compat` pre-commit hook bans it; use `frappe.qb` or the ORM.
- **Site runs PostgreSQL 18.4.** Never assume MariaDB dialect.
- **Patches go in `erpnext/patches/v16_0/`** and are appended to `patches.txt` below the `[post_model_sync]` marker, despite this being the v17 dev line.
- **`erpnext/modules.txt` has NO trailing newline** (ends `EDI`). A naive `echo >>` produces `EDIAI`. Always rewrite the file, never bare-append.
- **Secrets live in Password fields** read via `get_password()`, mirroring `TaskPilot Settings`. Env keys must stay in sync across `.env` and `.env.example` (already done for `PAPERCLIP_*`).
- **Tabs, not spaces**, for Python indentation in this repo.
- **The MCP endpoint must only ever be reached through Paperclip's tool gateway.** The CEO agent runs `dangerouslySkipPermissions: true`; wiring ERPNext into its local Claude Code MCP config would bypass every approval control. Task 11 adds an explicit check.
- **Company scoping is mandatory** on every doctype carrying a `company` field. Out-of-scope companies are refused, never silently re-scoped.

## File Structure

| Path | Responsibility |
|---|---|
| `erpnext/ai/__init__.py` | Module marker |
| `erpnext/ai/doctype/ai_settings/` | Single: Paperclip coords, bound company, role gate, caps |
| `erpnext/ai/registry.py` | Tool dataclass, decorator registration, enablement + cap enforcement |
| `erpnext/ai/mcp.py` | JSON-RPC transport: `initialize`, `tools/list`, `tools/call` |
| `erpnext/ai/scoping.py` | Bound-company resolution and filter injection |
| `erpnext/ai/knowledge.py` | Metadata introspection, workspace/URL location map, orientation text |
| `erpnext/ai/tools/discovery.py` | `get_company_context`, `search_doctypes`, `describe_doctype`, `list_reports`, `get_workspace_map` |
| `erpnext/ai/tools/documents.py` | `search_documents`, `get_document`, `create_document`, `update_document`, `submit_document`, `cancel_document`, `delete_document` |
| `erpnext/ai/tools/reports.py` | `run_report` |
| `erpnext/ai/tools/methods.py` | `call_method` |
| `erpnext/ai/paperclip.py` | Server-side Paperclip proxy for the SPA (key never reaches the browser) |
| `erpnext/ai/workspace/ai/ai.json` | Nav entry, `sequence_id: 1.5`, `type: Link → Page` |
| `erpnext/ai/page/ai/` | Desk Page shell that mounts the SPA bundle |
| `ai/` (repo root) | React SPA: thread, run-event feed, approval cards |
| `erpnext/ai/tests/` | Test package |

Tasks 1–7 deliver the backend and are independently useful (drivable from Paperclip with no ERPNext UI). Tasks 8–11 deliver the desk surface.

---

### Task 1: AI module scaffold and AI Settings

**Files:**
- Modify: `erpnext/modules.txt`
- Create: `erpnext/ai/__init__.py`, `erpnext/ai/doctype/__init__.py`, `erpnext/ai/doctype/ai_settings/__init__.py`
- Create: `erpnext/ai/doctype/ai_settings/ai_settings.json`
- Create: `erpnext/ai/doctype/ai_settings/ai_settings.py`
- Create: `erpnext/ai/tests/__init__.py`
- Test: `erpnext/ai/doctype/ai_settings/test_ai_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: doctype `AI Settings` (Single) with fields `enabled`, `paperclip_url`, `paperclip_company_id`, `board_api_key` (Password), `agent_id`, `erpnext_company` (Link→Company), `additional_companies` (Table MultiSelect→AI Company Item), `allowed_roles` (Table MultiSelect→Has Role), `enabled_tools` (Small Text JSON), `max_batch_size` (Int), `max_document_value` (Currency), `allowed_methods` (Code JSON). Plus `erpnext.ai.doctype.ai_settings.ai_settings.test_connection() -> dict`.

- [ ] **Step 1: Register the module without corrupting modules.txt**

`erpnext/modules.txt` ends with `EDI` and no newline. Rewrite it rather than appending:

```bash
cd /mnt/projects/erpnext
python3 - <<'PY'
import pathlib
p = pathlib.Path("erpnext/modules.txt")
lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
if "AI" not in lines:
    lines.append("AI")
p.write_text("\n".join(lines) + "\n")
PY
tail -3 erpnext/modules.txt
```

Expected output ends with `EDI` then `AI` on its own line.

- [ ] **Step 2: Create the package files**

```bash
mkdir -p erpnext/ai/doctype/ai_settings erpnext/ai/tests erpnext/ai/tools
for f in erpnext/ai/__init__.py erpnext/ai/doctype/__init__.py \
         erpnext/ai/doctype/ai_settings/__init__.py \
         erpnext/ai/tests/__init__.py erpnext/ai/tools/__init__.py; do
  touch "$f"
done
```

- [ ] **Step 3: Write the failing test**

Create `erpnext/ai/doctype/ai_settings/test_ai_settings.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestAISettings(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_enabling_without_credentials_is_rejected(self):
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = ""
		doc.paperclip_company_id = ""
		doc.agent_id = ""
		doc.erpnext_company = ""
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_enabling_requires_a_bound_company(self):
		company = frappe.db.get_value("Company", {}, "name")
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com/"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		doc.erpnext_company = ""
		self.assertRaises(frappe.ValidationError, doc.save)

		doc.erpnext_company = company
		doc.save()
		# trailing slash is normalised away so URL joins never double up
		self.assertEqual(doc.paperclip_url, "https://paperclip.example.com")

	def test_defaults_are_conservative(self):
		doc = frappe.get_single("AI Settings")
		self.assertEqual(doc.max_batch_size, 20)
		self.assertEqual(doc.max_document_value, 0)
```

- [ ] **Step 4: Run it and confirm it fails**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext \
  --module erpnext.ai.doctype.ai_settings.test_ai_settings
```

Expected: FAIL — `DoesNotExistError: DocType AI Settings not found`.

- [ ] **Step 5: Create the child doctype for additional companies**

Create `erpnext/ai/doctype/ai_company_item/__init__.py` (empty) and `erpnext/ai/doctype/ai_company_item/ai_company_item.json`:

```json
{
 "actions": [],
 "creation": "2026-08-06 12:00:00.000000",
 "doctype": "DocType",
 "editable_grid": 1,
 "engine": "InnoDB",
 "field_order": ["company"],
 "fields": [
  {"fieldname": "company", "fieldtype": "Link", "label": "Company", "options": "Company", "in_list_view": 1, "reqd": 1}
 ],
 "istable": 1,
 "modified": "2026-08-06 12:00:00.000000",
 "module": "AI",
 "name": "AI Company Item",
 "owner": "Administrator",
 "permissions": [],
 "sort_field": "creation",
 "sort_order": "DESC",
 "states": []
}
```

Create `erpnext/ai/doctype/ai_company_item/ai_company_item.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AICompanyItem(Document):
	pass
```

- [ ] **Step 6: Create the AI Settings doctype JSON**

Create `erpnext/ai/doctype/ai_settings/ai_settings.json`:

```json
{
 "actions": [],
 "creation": "2026-08-06 12:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": [
  "enabled", "paperclip_url", "paperclip_company_id", "board_api_key", "agent_id",
  "column_break_1", "erpnext_company", "additional_companies", "allowed_roles",
  "section_limits", "enabled_tools", "max_batch_size", "column_break_2",
  "max_document_value", "allowed_methods"
 ],
 "fields": [
  {"fieldname": "enabled", "fieldtype": "Check", "label": "Enabled", "default": "0"},
  {"fieldname": "paperclip_url", "fieldtype": "Data", "label": "Paperclip URL",
   "description": "Base URL, e.g. https://paperclip.sudiptadhara.in", "mandatory_depends_on": "enabled"},
  {"fieldname": "paperclip_company_id", "fieldtype": "Data", "label": "Paperclip Company ID",
   "description": "Paperclip's org/tenant UUID. NOT the ERPNext Company.", "mandatory_depends_on": "enabled"},
  {"fieldname": "board_api_key", "fieldtype": "Password", "label": "Board API Key", "mandatory_depends_on": "enabled"},
  {"fieldname": "agent_id", "fieldtype": "Data", "label": "CEO Agent ID", "mandatory_depends_on": "enabled"},
  {"fieldname": "column_break_1", "fieldtype": "Column Break"},
  {"fieldname": "erpnext_company", "fieldtype": "Link", "label": "ERPNext Company", "options": "Company",
   "description": "The accounting entity the agent operates on.", "mandatory_depends_on": "enabled"},
  {"fieldname": "additional_companies", "fieldtype": "Table MultiSelect", "label": "Additional Companies",
   "options": "AI Company Item", "description": "Opt-in cross-company access. Empty by default."},
  {"fieldname": "allowed_roles", "fieldtype": "Small Text", "label": "Allowed Roles",
   "default": "[\"System Manager\"]", "description": "JSON list of roles permitted to use the AI workspace."},
  {"fieldname": "section_limits", "fieldtype": "Section Break", "label": "Safety Limits"},
  {"fieldname": "enabled_tools", "fieldtype": "Small Text", "label": "Enabled Tools",
   "description": "JSON list of tool names. Empty means all registered tools."},
  {"fieldname": "max_batch_size", "fieldtype": "Int", "label": "Max Batch Size", "default": "20"},
  {"fieldname": "column_break_2", "fieldtype": "Column Break"},
  {"fieldname": "max_document_value", "fieldtype": "Currency", "label": "Max Document Value",
   "default": "0", "description": "0 means unlimited."},
  {"fieldname": "allowed_methods", "fieldtype": "Small Text", "label": "Allowed Methods",
   "default": "[]", "description": "JSON list of dotted paths callable via call_method."}
 ],
 "issingle": 1,
 "modified": "2026-08-06 12:00:00.000000",
 "module": "AI",
 "name": "AI Settings",
 "owner": "Administrator",
 "permissions": [
  {"role": "System Manager", "read": 1, "write": 1}
 ],
 "sort_field": "creation",
 "sort_order": "DESC",
 "states": []
}
```

- [ ] **Step 7: Write the controller**

Create `erpnext/ai/doctype/ai_settings/ai_settings.py`:

```python
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
```

`test_connection` imports `erpnext.ai.paperclip`, built in Task 7. Until then the button errors on import — that is expected and the doctype tests do not touch it.

- [ ] **Step 8: Migrate and run the tests**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost migrate
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext \
  --module erpnext.ai.doctype.ai_settings.test_ai_settings
```

Expected: PASS, 3 tests.

- [ ] **Step 9: Commit**

```bash
git add erpnext/modules.txt erpnext/ai
git commit -m "feat(ai): add AI module and AI Settings single"
```

---

### Task 2: Tool registry with cap enforcement

**Files:**
- Create: `erpnext/ai/registry.py`
- Test: `erpnext/ai/tests/test_registry.py`

**Interfaces:**
- Consumes: `AI Settings` from Task 1.
- Produces:
  - `Tool` dataclass: `name: str`, `description: str`, `input_schema: dict`, `handler: Callable`, `tier: str`
  - `tool(name: str, description: str, input_schema: dict, tier: str = "free") -> Callable` — registration decorator
  - `get_tools() -> list[Tool]` — enabled tools only
  - `get_tool(name: str) -> Tool` — raises `ToolError` if unknown or disabled
  - `settings() -> Document` — cached `AI Settings`
  - `clamp_limit(requested: int | None) -> int`
  - `assert_value_within_cap(value: float, label: str) -> None`
  - `ToolError(Exception)` with attribute `code: int`
  - MCP tool `ping` — a transport health check. **This is a fifteenth tool beyond the spec's fourteen**, added deliberately so Tasks 2–3 can prove registration and transport before any real tool exists. Keep it: it makes "is the connection alive" answerable without touching business data.

- [ ] **Step 1: Write the failing test**

Create `erpnext/ai/tests/test_registry.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry


class TestRegistry(IntegrationTestCase):
	def setUp(self):
		registry.settings.cache_clear()

	def tearDown(self):
		registry.settings.cache_clear()
		frappe.db.rollback()

	def test_registered_tool_is_returned(self):
		names = [t.name for t in registry.get_tools()]
		self.assertIn("ping", names)

	def test_unknown_tool_raises(self):
		with self.assertRaises(registry.ToolError):
			registry.get_tool("no_such_tool")

	def test_enabled_tools_allowlist_hides_others(self):
		doc = frappe.get_single("AI Settings")
		doc.enabled_tools = '["ping"]'
		doc.save()
		registry.settings.cache_clear()
		self.assertEqual([t.name for t in registry.get_tools()], ["ping"])

	def test_limit_is_clamped_to_max_batch_size(self):
		doc = frappe.get_single("AI Settings")
		doc.max_batch_size = 5
		doc.save()
		registry.settings.cache_clear()
		self.assertEqual(registry.clamp_limit(100), 5)
		self.assertEqual(registry.clamp_limit(None), 5)
		self.assertEqual(registry.clamp_limit(2), 2)

	def test_value_cap_refuses_rather_than_truncating(self):
		doc = frappe.get_single("AI Settings")
		doc.max_document_value = 1000
		doc.save()
		registry.settings.cache_clear()
		registry.assert_value_within_cap(999, "Sales Order")
		with self.assertRaises(registry.ToolError):
			registry.assert_value_within_cap(1001, "Sales Order")

	def test_zero_value_cap_means_unlimited(self):
		doc = frappe.get_single("AI Settings")
		doc.max_document_value = 0
		doc.save()
		registry.settings.cache_clear()
		registry.assert_value_within_cap(10**9, "Sales Order")
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_registry
```

Expected: FAIL — `ModuleNotFoundError: No module named 'erpnext.ai.registry'`.

- [ ] **Step 3: Implement the registry**

Create `erpnext/ai/registry.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Tool registration plus the ERPNext-side safety caps.

Approval tiering is enforced by Paperclip's tool policies, not here. What lives
in this module is the second line of defence: an allowlist and blast-radius
caps that hold even if a policy is misconfigured or a different agent is
pointed at the MCP endpoint.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import frappe
from frappe import _

# JSON-RPC application error code. -32000 is the generic "server error" slot.
TOOL_ERROR_CODE = -32000


class ToolError(Exception):
	"""Raised when a tool cannot run. Surfaced to the agent as a readable message."""

	def __init__(self, message: str, code: int = TOOL_ERROR_CODE):
		super().__init__(message)
		self.code = code


@dataclass
class Tool:
	name: str
	description: str
	input_schema: dict
	handler: Callable[..., Any]
	tier: str = "free"
	tags: list[str] = field(default_factory=list)


_REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, input_schema: dict, tier: str = "free") -> Callable:
	"""Register a callable as an MCP tool.

	`tier` is advisory metadata surfaced in the tool description so Paperclip
	policies (and a human reading the catalogue) can see which calls are
	expected to require approval.
	"""

	def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
		_REGISTRY[name] = Tool(
			name=name, description=description, input_schema=input_schema, handler=fn, tier=tier
		)
		return fn

	return decorator


@lru_cache(maxsize=1)
def settings():
	return frappe.get_single("AI Settings")


def _json_list(raw: str | None) -> list[str]:
	raw = (raw or "").strip()
	if not raw:
		return []
	try:
		value = json.loads(raw)
	except ValueError:
		return []
	return [item for item in value if isinstance(item, str)]


def get_tools() -> list[Tool]:
	"""Registered tools filtered by the AI Settings allowlist (empty means all)."""
	_load_tool_modules()
	allow = _json_list(settings().enabled_tools)
	tools = list(_REGISTRY.values())
	if allow:
		tools = [t for t in tools if t.name in allow]
	return sorted(tools, key=lambda t: t.name)


def get_tool(name: str) -> Tool:
	for candidate in get_tools():
		if candidate.name == name:
			return candidate
	raise ToolError(_("Unknown or disabled tool: {0}").format(name))


def clamp_limit(requested: int | None) -> int:
	cap = int(settings().max_batch_size or 20)
	if not requested or requested < 1:
		return cap
	return min(int(requested), cap)


def assert_value_within_cap(value: float, label: str) -> None:
	cap = float(settings().max_document_value or 0)
	if cap and float(value or 0) > cap:
		# Refuse rather than trim: a silently shrunk document is worse than an error.
		raise ToolError(
			_("{0} value {1} exceeds the configured AI limit of {2}.").format(label, value, cap)
		)


def _load_tool_modules() -> None:
	"""Import tool modules so their decorators run. Idempotent."""
	from erpnext.ai.tools import discovery, documents, methods, reports  # noqa: F401


@tool(
	name="ping",
	description="Health check. Returns pong and the ERPNext site name. Tier: free.",
	input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def ping() -> dict:
	return {"pong": True, "site": frappe.local.site}
```

- [ ] **Step 4: Create empty tool modules so the import in `_load_tool_modules` resolves**

```bash
for m in discovery documents methods reports; do
  printf '# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors\n# For license information, please see license.txt\n' \
    > "erpnext/ai/tools/$m.py"
done
```

- [ ] **Step 5: Run the tests**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_registry
```

Expected: PASS, 6 tests.

- [ ] **Step 6: Commit**

```bash
git add erpnext/ai/registry.py erpnext/ai/tools erpnext/ai/tests/test_registry.py
git commit -m "feat(ai): tool registry with allowlist and blast-radius caps"
```

---

### Task 3: MCP JSON-RPC transport

**Files:**
- Create: `erpnext/ai/mcp.py`
- Test: `erpnext/ai/tests/test_mcp.py`

**Interfaces:**
- Consumes: `registry.get_tools`, `registry.get_tool`, `registry.ToolError`, `registry.settings`.
- Produces:
  - `handle() -> dict` — whitelisted POST endpoint at `/api/method/erpnext.ai.mcp.handle`
  - `dispatch(payload: dict) -> dict` — pure function, testable without a request
  - `PROTOCOL_VERSION: str`

- [ ] **Step 1: Write the failing test**

Create `erpnext/ai/tests/test_mcp.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import mcp, registry


class TestMCP(IntegrationTestCase):
	def setUp(self):
		registry.settings.cache_clear()
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
		doc.enabled_tools = ""
		doc.save()
		registry.settings.cache_clear()

	def tearDown(self):
		registry.settings.cache_clear()
		frappe.db.rollback()

	def test_initialize_returns_protocol_and_instructions(self):
		out = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
		self.assertEqual(out["id"], 1)
		self.assertEqual(out["result"]["protocolVersion"], mcp.PROTOCOL_VERSION)
		self.assertIn("tools", out["result"]["capabilities"])
		# Orientation must name the bound company so the agent is never guessing.
		self.assertIn("ERPNext", out["result"]["instructions"])

	def test_tools_list_exposes_schemas(self):
		out = mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
		names = [t["name"] for t in out["result"]["tools"]]
		self.assertIn("ping", names)
		entry = next(t for t in out["result"]["tools"] if t["name"] == "ping")
		self.assertEqual(entry["inputSchema"]["type"], "object")

	def test_tools_call_returns_text_content(self):
		out = mcp.dispatch(
			{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
			 "params": {"name": "ping", "arguments": {}}}
		)
		payload = json.loads(out["result"]["content"][0]["text"])
		self.assertTrue(payload["pong"])
		self.assertFalse(out["result"].get("isError", False))

	def test_tool_error_is_reported_as_content_not_transport_error(self):
		out = mcp.dispatch(
			{"jsonrpc": "2.0", "id": 4, "method": "tools/call",
			 "params": {"name": "nope", "arguments": {}}}
		)
		# MCP convention: tool failures are results with isError, so the model can
		# read and adapt rather than the transport blowing up.
		self.assertTrue(out["result"]["isError"])
		self.assertIn("nope", out["result"]["content"][0]["text"])

	def test_unknown_method_is_a_jsonrpc_error(self):
		out = mcp.dispatch({"jsonrpc": "2.0", "id": 5, "method": "bogus/thing", "params": {}})
		self.assertEqual(out["error"]["code"], -32601)

	def test_disabled_settings_refuses_everything(self):
		doc = frappe.get_single("AI Settings")
		doc.enabled = 0
		doc.save()
		registry.settings.cache_clear()
		out = mcp.dispatch({"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}})
		self.assertEqual(out["error"]["code"], -32001)

	def test_notification_returns_no_response(self):
		# JSON-RPC notifications have no id and must not be answered.
		self.assertIsNone(mcp.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_mcp
```

Expected: FAIL — `No module named 'erpnext.ai.mcp'`.

- [ ] **Step 3: Implement the transport**

Create `erpnext/ai/mcp.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""MCP server for ERPNext, served as a single Frappe whitelisted endpoint.

MCP's streamable-HTTP transport is JSON-RPC over POST; SSE is only needed for
server-initiated messages, which a pure tool server never sends. Living inside
Frappe means `frappe.session.user` is already correct, so document permissions,
user permissions and field-level permissions are enforced by Frappe itself
instead of being reimplemented behind a sidecar.
"""

import json
from typing import Any

import frappe
from frappe import _

from erpnext.ai import registry
from erpnext.ai.registry import ToolError

PROTOCOL_VERSION = "2025-06-18"

METHOD_NOT_FOUND = -32601
INVALID_REQUEST = -32600
PARSE_ERROR = -32700
AI_DISABLED = -32001


def _error(request_id: Any, code: int, message: str) -> dict:
	return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, result: dict) -> dict:
	return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _text_content(payload: Any, is_error: bool = False) -> dict:
	text = payload if isinstance(payload, str) else frappe.as_json(payload, indent=None)
	return {"content": [{"type": "text", "text": text}], "isError": is_error}


def dispatch(payload: dict) -> dict | None:
	"""Handle one JSON-RPC request. Returns None for notifications."""
	request_id = payload.get("id")
	method = payload.get("method")

	if request_id is None:
		# Notification: acknowledge by doing nothing. Answering would violate JSON-RPC.
		return None

	if not method:
		return _error(request_id, INVALID_REQUEST, _("Missing method"))

	if not registry.settings().enabled:
		return _error(request_id, AI_DISABLED, _("ERPNext AI is disabled in AI Settings."))

	params = payload.get("params") or {}

	if method == "initialize":
		from erpnext.ai.knowledge import orientation_text

		return _result(
			request_id,
			{
				"protocolVersion": PROTOCOL_VERSION,
				"capabilities": {"tools": {"listChanged": False}},
				"serverInfo": {"name": "erpnext", "version": frappe.__version__},
				"instructions": orientation_text(),
			},
		)

	if method == "tools/list":
		return _result(
			request_id,
			{
				"tools": [
					{"name": t.name, "description": t.description, "inputSchema": t.input_schema}
					for t in registry.get_tools()
				]
			},
		)

	if method == "tools/call":
		name = params.get("name") or ""
		arguments = params.get("arguments") or {}
		try:
			handler = registry.get_tool(name).handler
			return _result(request_id, _text_content(handler(**arguments)))
		except ToolError as exc:
			return _result(request_id, _text_content(str(exc), is_error=True))
		except frappe.PermissionError:
			# Deliberately uninformative about existence: saying "no such record"
			# vs "not permitted" would leak whether hidden data exists.
			return _result(
				request_id,
				_text_content(_("Not permitted for the current ERPNext user."), is_error=True),
			)
		except TypeError as exc:
			return _result(request_id, _text_content(_("Bad arguments: {0}").format(exc), is_error=True))
		except Exception as exc:
			frappe.log_error(title="ERPNext AI tool failure", message=frappe.get_traceback())
			return _result(request_id, _text_content(str(exc), is_error=True))

	return _error(request_id, METHOD_NOT_FOUND, _("Unknown method: {0}").format(method))


@frappe.whitelist(methods=["POST"])
def handle() -> dict | None:
	"""MCP endpoint. POST JSON-RPC to /api/method/erpnext.ai.mcp.handle.

	The body is read raw rather than through form_dict: JSON-RPC uses a key
	called `method`, which would collide with Frappe's own request kwargs.
	"""
	raw = frappe.request.get_data(as_text=True) if frappe.request else ""
	try:
		payload = json.loads(raw or "{}")
	except ValueError:
		return _error(None, PARSE_ERROR, _("Invalid JSON"))

	if isinstance(payload, list):
		responses = [r for r in (dispatch(item) for item in payload) if r is not None]
		return responses or None

	return dispatch(payload)
```

- [ ] **Step 4: Add a minimal `orientation_text` so `initialize` resolves**

Create `erpnext/ai/knowledge.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Facts about this ERPNext install, generated live so they cannot go stale."""

import frappe
from frappe import _

from erpnext.ai import registry


def orientation_text() -> str:
	"""System instructions handed to the agent on every MCP connection."""
	company = registry.settings().erpnext_company
	details = frappe.db.get_value(
		"Company", company, ["default_currency", "country"], as_dict=True
	) or frappe._dict()

	return _(
		"You are connected to ERPNext {version}, an ERP system, over MCP.\n\n"
		"Operating company: {company} (currency {currency}, country {country}). "
		"Unless a tool argument says otherwise, every query is scoped to this company; "
		"requesting a different company is refused rather than silently re-scoped.\n\n"
		"Document lifecycle: documents are created as DRAFTS (docstatus 0) and are freely "
		"reversible. SUBMIT (docstatus 1) posts to the general ledger and stock ledger and is "
		"not freely reversible. Tools marked 'Tier: approval' therefore pause for a human "
		"decision before they run — expect that and do not retry them in a loop.\n\n"
		"Start with get_company_context to ground yourself, search_doctypes to find the right "
		"record type, and describe_doctype to learn its fields before writing anything. "
		"All permissions are enforced by ERPNext for the connected user."
	).format(
		version=frappe.__version__,
		company=company or _("not configured"),
		currency=details.get("default_currency") or _("unknown"),
		country=details.get("country") or _("unknown"),
	)
```

- [ ] **Step 5: Run the tests**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_mcp
```

Expected: PASS, 7 tests.

- [ ] **Step 6: Commit**

```bash
git add erpnext/ai/mcp.py erpnext/ai/knowledge.py erpnext/ai/tests/test_mcp.py
git commit -m "feat(ai): MCP JSON-RPC transport with live-generated orientation"
```

---

### Task 4: Company scoping

**Files:**
- Create: `erpnext/ai/scoping.py`
- Test: `erpnext/ai/tests/test_scoping.py`

**Interfaces:**
- Consumes: `registry.settings`, `registry.ToolError`.
- Produces:
  - `bound_companies() -> list[str]`
  - `assert_company_allowed(company: str) -> None`
  - `has_company_field(doctype: str) -> bool`
  - `scope_filters(doctype: str, filters: dict | None) -> dict`
  - `default_company() -> str`

- [ ] **Step 1: Write the failing test**

Create `erpnext/ai/tests/test_scoping.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry, scoping


class TestScoping(IntegrationTestCase):
	def setUp(self):
		registry.settings.cache_clear()
		self.company = frappe.db.get_value("Company", {}, "name")
		doc = frappe.get_single("AI Settings")
		doc.erpnext_company = self.company
		doc.additional_companies = []
		doc.save()
		registry.settings.cache_clear()

	def tearDown(self):
		registry.settings.cache_clear()
		frappe.db.rollback()

	def test_bound_companies_defaults_to_one(self):
		self.assertEqual(scoping.bound_companies(), [self.company])

	def test_out_of_scope_company_is_refused_not_rescoped(self):
		with self.assertRaises(registry.ToolError):
			scoping.assert_company_allowed("Some Other Entity Ltd")

	def test_company_field_detection(self):
		self.assertTrue(scoping.has_company_field("Sales Invoice"))
		self.assertFalse(scoping.has_company_field("Currency"))

	def test_filters_get_company_injected(self):
		out = scoping.scope_filters("Sales Invoice", {"status": "Draft"})
		self.assertEqual(out["company"], self.company)
		self.assertEqual(out["status"], "Draft")

	def test_explicit_in_scope_company_is_preserved(self):
		out = scoping.scope_filters("Sales Invoice", {"company": self.company})
		self.assertEqual(out["company"], self.company)

	def test_explicit_out_of_scope_company_raises(self):
		with self.assertRaises(registry.ToolError):
			scoping.scope_filters("Sales Invoice", {"company": "Nope Ltd"})

	def test_non_company_doctype_is_untouched(self):
		out = scoping.scope_filters("Currency", {"enabled": 1})
		self.assertNotIn("company", out)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_scoping
```

Expected: FAIL — `No module named 'erpnext.ai.scoping'`.

- [ ] **Step 3: Implement scoping**

Create `erpnext/ai/scoping.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Company scoping.

`company` is a mandatory filter on nearly every transactional doctype. An agent
that does not know which company it operates in produces ambiguous queries, and
on a multi-entity site would mix the books of separate legal entities.
"""

import frappe
from frappe import _

from erpnext.ai import registry
from erpnext.ai.registry import ToolError


def default_company() -> str:
	company = registry.settings().erpnext_company
	if not company:
		raise ToolError(_("No ERPNext Company is bound in AI Settings."))
	return company


def bound_companies() -> list[str]:
	settings = registry.settings()
	companies = [settings.erpnext_company] if settings.erpnext_company else []
	companies += [row.company for row in (settings.additional_companies or []) if row.company]
	return companies


def assert_company_allowed(company: str) -> None:
	allowed = bound_companies()
	if company not in allowed:
		# Refusing is deliberate. An agent reaching for the wrong legal entity is
		# a bug worth surfacing, not something to quietly correct.
		raise ToolError(
			_("Company {0} is out of scope. This agent may only access: {1}.").format(
				company, ", ".join(allowed) or _("none")
			)
		)


def has_company_field(doctype: str) -> bool:
	return bool(frappe.get_meta(doctype).get_field("company"))


def scope_filters(doctype: str, filters: dict | None) -> dict:
	"""Return filters with the bound company applied, validating any explicit one."""
	result = dict(filters or {})
	if not has_company_field(doctype):
		return result

	explicit = result.get("company")
	if explicit:
		if isinstance(explicit, str):
			assert_company_allowed(explicit)
		return result

	result["company"] = default_company()
	return result
```

- [ ] **Step 4: Run the tests**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_scoping
```

Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add erpnext/ai/scoping.py erpnext/ai/tests/test_scoping.py
git commit -m "feat(ai): company scoping that refuses out-of-scope entities"
```

---

### Task 5: Discovery and knowledge tools

**Files:**
- Modify: `erpnext/ai/knowledge.py`
- Modify: `erpnext/ai/tools/discovery.py`
- Test: `erpnext/ai/tests/test_discovery.py`

**Interfaces:**
- Consumes: `scoping.default_company`, `registry.tool`, `registry.clamp_limit`.
- Produces MCP tools `get_company_context`, `search_doctypes`, `describe_doctype`, `list_reports`, `get_workspace_map`, plus `knowledge.workspace_map() -> dict[str, dict]`.

- [ ] **Step 1: Write the failing test**

Create `erpnext/ai/tests/test_discovery.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry
from erpnext.ai.tools import discovery


class TestDiscovery(IntegrationTestCase):
	def setUp(self):
		registry.settings.cache_clear()
		doc = frappe.get_single("AI Settings")
		doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
		doc.save()
		registry.settings.cache_clear()

	def tearDown(self):
		registry.settings.cache_clear()
		frappe.db.rollback()

	def test_company_context_grounds_the_agent(self):
		out = discovery.get_company_context()
		self.assertEqual(out["company"], frappe.get_single("AI Settings").erpnext_company)
		self.assertTrue(out["currency"])
		self.assertIn("modules", out)

	def test_search_doctypes_finds_by_label(self):
		names = [d["doctype"] for d in discovery.search_doctypes("sales invoice")]
		self.assertIn("Sales Invoice", names)

	def test_search_doctypes_includes_desk_url(self):
		hit = next(d for d in discovery.search_doctypes("sales invoice") if d["doctype"] == "Sales Invoice")
		self.assertEqual(hit["url"], "/app/sales-invoice")

	def test_describe_doctype_reports_fields_and_permissions(self):
		out = discovery.describe_doctype("Sales Invoice")
		fieldnames = [f["fieldname"] for f in out["fields"]]
		self.assertIn("customer", fieldnames)
		self.assertTrue(out["is_submittable"])
		self.assertIn("read", out["permissions"])

	def test_describe_unknown_doctype_raises_tool_error(self):
		with self.assertRaises(registry.ToolError):
			discovery.describe_doctype("Not A Real Doctype")

	def test_list_reports_returns_entries(self):
		out = discovery.list_reports("Accounts")
		self.assertTrue(out)
		self.assertIn("name", out[0])

	def test_workspace_map_locates_doctypes(self):
		out = discovery.get_workspace_map()
		self.assertIn("Sales Invoice", out)
		self.assertIn("workspace", out["Sales Invoice"])
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_discovery
```

Expected: FAIL — `module 'erpnext.ai.tools.discovery' has no attribute 'get_company_context'`.

- [ ] **Step 3: Add `workspace_map` to knowledge.py**

Append to `erpnext/ai/knowledge.py`:

```python
def workspace_map() -> dict[str, dict]:
	"""Doctype -> {workspace, module, url}.

	Answers "where is what" from this install's own Workspace links, so it
	reflects customisations that no published manual ever will.
	"""
	links = frappe.get_all(
		"Workspace Link",
		filters={"link_type": "DocType", "type": "Link"},
		fields=["link_to", "parent", "label"],
	)
	mapping: dict[str, dict] = {}
	for link in links:
		if not link.link_to or link.link_to in mapping:
			continue
		mapping[link.link_to] = {
			"workspace": link.parent,
			"module": frappe.db.get_value("DocType", link.link_to, "module"),
			"url": desk_url(link.link_to),
		}
	return mapping


def desk_url(doctype: str) -> str:
	return "/app/" + frappe.scrub(doctype).replace("_", "-")
```

- [ ] **Step 4: Implement the discovery tools**

Replace `erpnext/ai/tools/discovery.py` with:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Grounding and discovery tools.

ERPNext is self-describing, so these read live metadata rather than a scraped
document that would drift from the running install.
"""

import frappe
from frappe import _

from erpnext.ai import scoping
from erpnext.ai.knowledge import desk_url, workspace_map
from erpnext.ai.registry import ToolError, clamp_limit, tool

_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}


@tool(
	name="get_company_context",
	description=(
		"Ground yourself: returns the operating company, its currency, country, fiscal year, "
		"chart-of-accounts roots, active modules and headline record counts. "
		"Call this first in any new conversation. Tier: free."
	),
	input_schema=_NO_ARGS,
)
def get_company_context() -> dict:
	company = scoping.default_company()
	details = (
		frappe.db.get_value("Company", company, ["default_currency", "country", "abbr"], as_dict=True)
		or frappe._dict()
	)
	fiscal_year = frappe.get_all(
		"Fiscal Year", fields=["name", "year_start_date", "year_end_date"], order_by="year_start_date desc", limit=1
	)
	roots = frappe.get_all(
		"Account",
		filters={"company": company, "parent_account": ["in", [None, ""]]},
		fields=["name", "root_type"],
	)
	return {
		"company": company,
		"abbreviation": details.get("abbr"),
		"currency": details.get("default_currency"),
		"country": details.get("country"),
		"fiscal_year": fiscal_year[0] if fiscal_year else None,
		"chart_of_accounts_roots": roots,
		"modules": frappe.get_all("Module Def", filters={"app_name": "erpnext"}, pluck="name"),
		"accessible_companies": scoping.bound_companies(),
		"counts": {
			doctype: frappe.db.count(doctype)
			for doctype in ("Customer", "Supplier", "Item", "Sales Order", "Sales Invoice")
		},
	}


@tool(
	name="search_doctypes",
	description=(
		"Find ERPNext record types by name or purpose. Returns each doctype with its module, "
		"owning workspace and desk URL, so you can also answer 'where is that in the UI'. Tier: free."
	),
	input_schema={
		"type": "object",
		"properties": {
			"query": {"type": "string", "description": "Free text, e.g. 'overdue invoice' or 'stock entry'"},
			"module": {"type": "string", "description": "Optional module filter, e.g. 'Accounts'"},
			"limit": {"type": "integer"},
		},
		"required": ["query"],
		"additionalProperties": False,
	},
)
def search_doctypes(query: str, module: str | None = None, limit: int | None = None) -> list[dict]:
	filters: dict = {"istable": 0}
	if module:
		filters["module"] = module

	rows = frappe.get_all(
		"DocType",
		filters=filters,
		or_filters={"name": ["like", f"%{query}%"], "description": ["like", f"%{query}%"]},
		fields=["name", "module", "description", "issingle", "is_submittable"],
		limit=clamp_limit(limit),
	)
	locations = workspace_map()
	return [
		{
			"doctype": row.name,
			"module": row.module,
			"description": row.description,
			"is_single": bool(row.issingle),
			"is_submittable": bool(row.is_submittable),
			"workspace": (locations.get(row.name) or {}).get("workspace"),
			"url": desk_url(row.name),
		}
		for row in rows
	]


@tool(
	name="describe_doctype",
	description=(
		"Full schema for one doctype: fields with types and options, link targets, mandatory flags, "
		"naming, whether it is submittable, and YOUR effective permissions on it. "
		"Read this before creating or updating records. Tier: free."
	),
	input_schema={
		"type": "object",
		"properties": {"doctype": {"type": "string"}},
		"required": ["doctype"],
		"additionalProperties": False,
	},
)
def describe_doctype(doctype: str) -> dict:
	if not frappe.db.exists("DocType", doctype):
		raise ToolError(_("No such doctype: {0}").format(doctype))

	meta = frappe.get_meta(doctype)
	fields = [
		{
			"fieldname": f.fieldname,
			"label": f.label,
			"fieldtype": f.fieldtype,
			"options": f.options,
			"reqd": bool(f.reqd),
			"read_only": bool(f.read_only),
			"default": f.default,
		}
		for f in meta.fields
		if f.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML")
	]
	return {
		"doctype": doctype,
		"module": meta.module,
		"is_submittable": bool(meta.is_submittable),
		"is_single": bool(meta.issingle),
		"is_tree": bool(meta.get("is_tree")),
		"autoname": meta.autoname,
		"title_field": meta.title_field,
		"fields": fields,
		"child_tables": [
			{"fieldname": f.fieldname, "doctype": f.options}
			for f in meta.fields
			if f.fieldtype in ("Table", "Table MultiSelect")
		],
		"permissions": [
			action
			for action in ("read", "write", "create", "delete", "submit", "cancel")
			if frappe.has_permission(doctype, ptype=action)
		],
		"url": desk_url(doctype),
	}


@tool(
	name="list_reports",
	description="List available ERPNext reports, optionally filtered by module. Tier: free.",
	input_schema={
		"type": "object",
		"properties": {"module": {"type": "string"}, "limit": {"type": "integer"}},
		"additionalProperties": False,
	},
)
def list_reports(module: str | None = None, limit: int | None = None) -> list[dict]:
	filters = {"disabled": 0}
	if module:
		filters["module"] = module
	return frappe.get_all(
		"Report",
		filters=filters,
		fields=["name", "module", "report_type", "ref_doctype"],
		limit=clamp_limit(limit),
	)


@tool(
	name="get_workspace_map",
	description=(
		"Map of doctype -> {workspace, module, desk URL} for this install. "
		"Use to answer 'where do I do X in the UI'. Tier: free."
	),
	input_schema=_NO_ARGS,
)
def get_workspace_map() -> dict:
	return workspace_map()
```

- [ ] **Step 5: Run the tests**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_discovery
```

Expected: PASS, 7 tests.

- [ ] **Step 6: Commit**

```bash
git add erpnext/ai/knowledge.py erpnext/ai/tools/discovery.py erpnext/ai/tests/test_discovery.py
git commit -m "feat(ai): discovery tools driven by live metadata"
```

---

### Task 6: Read tools

**Files:**
- Modify: `erpnext/ai/tools/documents.py`
- Modify: `erpnext/ai/tools/reports.py`
- Test: `erpnext/ai/tests/test_read_tools.py`

**Interfaces:**
- Consumes: `scoping.scope_filters`, `registry.clamp_limit`, `registry.ToolError`.
- Produces MCP tools `search_documents`, `get_document`, `run_report`.

- [ ] **Step 1: Write the failing test**

Create `erpnext/ai/tests/test_read_tools.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry
from erpnext.ai.tools import documents, reports


class TestReadTools(IntegrationTestCase):
	def setUp(self):
		registry.settings.cache_clear()
		self.company = frappe.db.get_value("Company", {}, "name")
		doc = frappe.get_single("AI Settings")
		doc.erpnext_company = self.company
		doc.max_batch_size = 5
		doc.save()
		registry.settings.cache_clear()

	def tearDown(self):
		registry.settings.cache_clear()
		frappe.db.rollback()

	def test_search_documents_returns_rows(self):
		out = documents.search_documents("Company", fields=["name"])
		self.assertTrue(any(row["name"] == self.company for row in out))

	def test_search_documents_clamps_limit(self):
		out = documents.search_documents("DocType", fields=["name"], limit=1000)
		self.assertLessEqual(len(out), 5)

	def test_search_documents_rejects_out_of_scope_company(self):
		with self.assertRaises(registry.ToolError):
			documents.search_documents("Sales Invoice", filters={"company": "Nope Ltd"})

	def test_get_document_includes_child_tables(self):
		out = documents.get_document("Company", self.company)
		self.assertEqual(out["name"], self.company)
		self.assertEqual(out["doctype"], "Company")

	def test_get_missing_document_raises_tool_error(self):
		with self.assertRaises(registry.ToolError):
			documents.get_document("Company", "No Such Company Ltd")

	def test_run_report_rejects_unknown_report(self):
		with self.assertRaises(registry.ToolError):
			reports.run_report("Not A Report")
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_read_tools
```

Expected: FAIL — `module 'erpnext.ai.tools.documents' has no attribute 'search_documents'`.

- [ ] **Step 3: Implement the read half of documents.py**

Replace `erpnext/ai/tools/documents.py` with:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Generic document tools.

ERPNext's surface is uniform — every doctype is CRUD plus optional
submit/cancel — so these are generic and metadata-driven rather than
hand-written per doctype.
"""

import frappe
from frappe import _

from erpnext.ai import scoping
from erpnext.ai.registry import ToolError, assert_value_within_cap, clamp_limit, tool


@tool(
	name="search_documents",
	description=(
		"List records of a doctype. `filters` uses Frappe syntax: {'status': 'Draft'} or "
		"{'grand_total': ['>', 1000]}. Automatically scoped to the operating company. Tier: free."
	),
	input_schema={
		"type": "object",
		"properties": {
			"doctype": {"type": "string"},
			"filters": {"type": "object"},
			"fields": {"type": "array", "items": {"type": "string"}},
			"order_by": {"type": "string"},
			"limit": {"type": "integer"},
			"start": {"type": "integer"},
		},
		"required": ["doctype"],
		"additionalProperties": False,
	},
)
def search_documents(
	doctype: str,
	filters: dict | None = None,
	fields: list[str] | None = None,
	order_by: str | None = None,
	limit: int | None = None,
	start: int | None = None,
) -> list[dict]:
	if not frappe.db.exists("DocType", doctype):
		raise ToolError(_("No such doctype: {0}").format(doctype))

	return frappe.get_list(
		doctype,
		filters=scoping.scope_filters(doctype, filters),
		fields=fields or ["name"],
		order_by=order_by or "modified desc",
		limit_page_length=clamp_limit(limit),
		limit_start=start or 0,
	)


@tool(
	name="get_document",
	description="Fetch one document in full, including its child tables. Tier: free.",
	input_schema={
		"type": "object",
		"properties": {"doctype": {"type": "string"}, "name": {"type": "string"}},
		"required": ["doctype", "name"],
		"additionalProperties": False,
	},
)
def get_document(doctype: str, name: str) -> dict:
	if not frappe.db.exists(doctype, name):
		raise ToolError(_("{0} {1} not found.").format(doctype, name))

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	if scoping.has_company_field(doctype) and doc.get("company"):
		scoping.assert_company_allowed(doc.company)
	return doc.as_dict(no_default_fields=False)
```

- [ ] **Step 4: Implement reports.py**

Replace `erpnext/ai/tools/reports.py` with:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.desk.query_report import run as run_query_report

from erpnext.ai import scoping
from erpnext.ai.registry import ToolError, tool


@tool(
	name="run_report",
	description=(
		"Execute an ERPNext report and return its columns and rows. Use list_reports to discover "
		"names. The operating company is injected into filters automatically. Tier: free."
	),
	input_schema={
		"type": "object",
		"properties": {"report": {"type": "string"}, "filters": {"type": "object"}},
		"required": ["report"],
		"additionalProperties": False,
	},
)
def run_report(report: str, filters: dict | None = None) -> dict:
	if not frappe.db.exists("Report", report):
		raise ToolError(_("No such report: {0}").format(report))

	frappe.get_doc("Report", report).check_permission("read")

	applied = dict(filters or {})
	# Most ERPNext reports take a `company` filter; supply it rather than making
	# the agent guess, and validate any it passed itself.
	if "company" in applied:
		scoping.assert_company_allowed(applied["company"])
	else:
		applied["company"] = scoping.default_company()

	result = run_query_report(report_name=report, filters=applied, ignore_prepared_report=True)
	return {
		"columns": result.get("columns") or [],
		"rows": result.get("result") or [],
		"filters": applied,
	}
```

- [ ] **Step 5: Run the tests**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_read_tools
```

Expected: PASS, 6 tests.

- [ ] **Step 6: Add the permission test that runs THROUGH the MCP path**

Spec §8 requires proving denial through the transport, not just trusting Frappe's permission layer. Append to `erpnext/ai/tests/test_read_tools.py`:

```python
class TestMCPPermissionBoundary(IntegrationTestCase):
	"""A low-privilege user must be refused through tools/call, not just in the ORM."""

	def setUp(self):
		registry.settings.cache_clear()
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
		doc.save()
		registry.settings.cache_clear()

		self.user_email = "ai-lowpriv@example.com"
		if not frappe.db.exists("User", self.user_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": self.user_email,
					"first_name": "Low Priv",
					"roles": [{"role": "Blogger"}],
				}
			).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		registry.settings.cache_clear()
		frappe.db.rollback()

	def test_low_privilege_user_is_denied_through_tools_call(self):
		from erpnext.ai import mcp

		frappe.set_user(self.user_email)
		out = mcp.dispatch(
			{
				"jsonrpc": "2.0",
				"id": 1,
				"method": "tools/call",
				"params": {"name": "search_documents", "arguments": {"doctype": "GL Entry"}},
			}
		)
		self.assertTrue(out["result"]["isError"])
		text = out["result"]["content"][0]["text"]
		# The message must state refusal without revealing whether records exist.
		self.assertIn("Not permitted", text)
		self.assertNotIn("rows", text.lower())
```

- [ ] **Step 7: Run the tests**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_read_tools
```

Expected: PASS, 7 tests.

- [ ] **Step 8: Commit**

```bash
git add erpnext/ai/tools/documents.py erpnext/ai/tools/reports.py erpnext/ai/tests/test_read_tools.py
git commit -m "feat(ai): read tools with company scoping, clamping and MCP permission test"
```

---

### Task 7: Write tools, idempotency and method calls

**Files:**
- Modify: `erpnext/ai/tools/documents.py`
- Modify: `erpnext/ai/tools/methods.py`
- Test: `erpnext/ai/tests/test_write_tools.py`

**Interfaces:**
- Consumes: everything from Tasks 4–6.
- Produces MCP tools `create_document`, `update_document`, `submit_document`, `cancel_document`, `delete_document`, `call_method`.

**Idempotency:** `create_document` requires an `idempotency_key`, stored in a dedicated `AI Idempotency` doctype mapping key → created document. Agent runs retry; without this, one network blip during an unattended run invents duplicate invoices.

- [ ] **Step 1: Create the idempotency doctype**

```bash
mkdir -p erpnext/ai/doctype/ai_idempotency_record
touch erpnext/ai/doctype/ai_idempotency_record/__init__.py
```

Create `erpnext/ai/doctype/ai_idempotency_record/ai_idempotency_record.json`:

```json
{
 "actions": [],
 "autoname": "field:idempotency_key",
 "creation": "2026-08-06 12:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": ["idempotency_key", "ref_doctype", "ref_name"],
 "fields": [
  {"fieldname": "idempotency_key", "fieldtype": "Data", "label": "Idempotency Key", "unique": 1, "reqd": 1},
  {"fieldname": "ref_doctype", "fieldtype": "Link", "label": "Reference DocType", "options": "DocType", "reqd": 1},
  {"fieldname": "ref_name", "fieldtype": "Dynamic Link", "label": "Reference Name", "options": "ref_doctype", "reqd": 1}
 ],
 "modified": "2026-08-06 12:00:00.000000",
 "module": "AI",
 "name": "AI Idempotency Record",
 "owner": "Administrator",
 "permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1}],
 "sort_field": "creation",
 "sort_order": "DESC",
 "states": []
}
```

Create `erpnext/ai/doctype/ai_idempotency_record/ai_idempotency_record.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIIdempotencyRecord(Document):
	pass
```

- [ ] **Step 2: Write the failing test**

Create `erpnext/ai/tests/test_write_tools.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import registry
from erpnext.ai.tools import documents, methods


class TestWriteTools(IntegrationTestCase):
	def setUp(self):
		registry.settings.cache_clear()
		doc = frappe.get_single("AI Settings")
		doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
		doc.max_document_value = 0
		doc.allowed_methods = '["erpnext.ai.tools.methods.echo"]'
		doc.save()
		registry.settings.cache_clear()

	def tearDown(self):
		registry.settings.cache_clear()
		frappe.db.rollback()

	def test_create_document_makes_a_draft(self):
		out = documents.create_document(
			"Note", {"title": "AI test note"}, idempotency_key="k-create-1"
		)
		self.assertEqual(out["doctype"], "Note")
		self.assertFalse(out["reused"])

	def test_repeat_idempotency_key_returns_the_original(self):
		first = documents.create_document("Note", {"title": "once"}, idempotency_key="k-dup")
		second = documents.create_document("Note", {"title": "once"}, idempotency_key="k-dup")
		self.assertEqual(first["name"], second["name"])
		self.assertTrue(second["reused"])

	def test_create_requires_idempotency_key(self):
		with self.assertRaises(TypeError):
			documents.create_document("Note", {"title": "no key"})

	def test_update_rejects_submitted_documents(self):
		note = frappe.get_doc({"doctype": "Note", "title": "immutable"}).insert()
		# Simulate a submitted doc; update_document must refuse regardless of doctype.
		frappe.db.set_value("Note", note.name, "docstatus", 1)
		with self.assertRaises(registry.ToolError):
			documents.update_document("Note", note.name, {"title": "changed"})

	def test_delete_removes_the_document(self):
		note = frappe.get_doc({"doctype": "Note", "title": "to delete"}).insert()
		out = documents.delete_document("Note", note.name)
		self.assertTrue(out["deleted"])
		self.assertFalse(frappe.db.exists("Note", note.name))

	def test_delete_of_missing_document_raises_tool_error(self):
		with self.assertRaises(registry.ToolError):
			documents.delete_document("Note", "no-such-note")

	def test_call_method_refuses_unlisted_paths(self):
		with self.assertRaises(registry.ToolError):
			methods.call_method("frappe.db.sql", {})

	def test_call_method_allows_listed_path(self):
		out = methods.call_method("erpnext.ai.tools.methods.echo", {"value": "hi"})
		self.assertEqual(out, "hi")
```

- [ ] **Step 3: Run it and confirm it fails**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend bench --site erp.localhost migrate
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_write_tools
```

Expected: FAIL — `module 'erpnext.ai.tools.documents' has no attribute 'create_document'`.

- [ ] **Step 4: Append the write tools to documents.py**

Append to `erpnext/ai/tools/documents.py`:

```python
def _remember_idempotency(key: str, doctype: str, name: str) -> None:
	frappe.get_doc(
		{
			"doctype": "AI Idempotency Record",
			"idempotency_key": key,
			"ref_doctype": doctype,
			"ref_name": name,
		}
	).insert(ignore_permissions=True)


def _recall_idempotency(key: str) -> dict | None:
	row = frappe.db.get_value(
		"AI Idempotency Record", {"idempotency_key": key}, ["ref_doctype", "ref_name"], as_dict=True
	)
	if not row:
		return None
	if not frappe.db.exists(row.ref_doctype, row.ref_name):
		return None
	return {"doctype": row.ref_doctype, "name": row.ref_name}


def _assert_value_cap(doc) -> None:
	for fieldname in ("grand_total", "base_grand_total", "total"):
		value = doc.get(fieldname)
		if value:
			assert_value_within_cap(value, f"{doc.doctype} {doc.name or ''}".strip())
			return


@tool(
	name="create_document",
	description=(
		"Create a record. It is always created as a DRAFT (docstatus 0) and is reversible. "
		"`idempotency_key` is REQUIRED: reusing a key returns the previously created document "
		"instead of creating a duplicate, so retries are safe. Tier: free."
	),
	input_schema={
		"type": "object",
		"properties": {
			"doctype": {"type": "string"},
			"data": {"type": "object"},
			"idempotency_key": {
				"type": "string",
				"description": "Stable unique string for this logical creation, e.g. 'quote-acme-2026-08-06'",
			},
		},
		"required": ["doctype", "data", "idempotency_key"],
		"additionalProperties": False,
	},
)
def create_document(doctype: str, data: dict, idempotency_key: str) -> dict:
	existing = _recall_idempotency(idempotency_key)
	if existing:
		return {**existing, "reused": True}

	if not frappe.db.exists("DocType", doctype):
		raise ToolError(_("No such doctype: {0}").format(doctype))

	payload = dict(data or {})
	payload["doctype"] = doctype
	if scoping.has_company_field(doctype):
		if payload.get("company"):
			scoping.assert_company_allowed(payload["company"])
		else:
			payload["company"] = scoping.default_company()

	doc = frappe.get_doc(payload)
	_assert_value_cap(doc)
	doc.insert()
	_remember_idempotency(idempotency_key, doctype, doc.name)
	return {"doctype": doctype, "name": doc.name, "docstatus": doc.docstatus, "reused": False}


@tool(
	name="update_document",
	description=(
		"Change fields on a DRAFT document. Submitted documents are refused here — cancel and amend "
		"instead. Tier: free for drafts, approval for anything submitted."
	),
	input_schema={
		"type": "object",
		"properties": {
			"doctype": {"type": "string"},
			"name": {"type": "string"},
			"data": {"type": "object"},
		},
		"required": ["doctype", "name", "data"],
		"additionalProperties": False,
	},
)
def update_document(doctype: str, name: str, data: dict) -> dict:
	doc = _load_for_write(doctype, name)
	if doc.docstatus != 0:
		raise ToolError(
			_("{0} {1} is submitted or cancelled; it cannot be edited directly.").format(doctype, name)
		)

	for key, value in (data or {}).items():
		if key == "company":
			scoping.assert_company_allowed(value)
		doc.set(key, value)

	_assert_value_cap(doc)
	doc.save()
	return {"doctype": doctype, "name": doc.name, "docstatus": doc.docstatus}


@tool(
	name="submit_document",
	description=(
		"Submit a draft. This POSTS TO THE GENERAL LEDGER and stock ledger and is not freely "
		"reversible. Tier: approval — expect this call to pause for a human decision."
	),
	input_schema={
		"type": "object",
		"properties": {"doctype": {"type": "string"}, "name": {"type": "string"}},
		"required": ["doctype", "name"],
		"additionalProperties": False,
	},
	tier="approval",
)
def submit_document(doctype: str, name: str) -> dict:
	doc = _load_for_write(doctype, name)
	_assert_value_cap(doc)
	doc.submit()
	return {"doctype": doctype, "name": doc.name, "docstatus": doc.docstatus}


@tool(
	name="cancel_document",
	description="Cancel a submitted document, reversing its ledger entries. Tier: approval.",
	input_schema={
		"type": "object",
		"properties": {"doctype": {"type": "string"}, "name": {"type": "string"}},
		"required": ["doctype", "name"],
		"additionalProperties": False,
	},
	tier="approval",
)
def cancel_document(doctype: str, name: str) -> dict:
	doc = _load_for_write(doctype, name)
	doc.cancel()
	return {"doctype": doctype, "name": doc.name, "docstatus": doc.docstatus}


@tool(
	name="delete_document",
	description="Permanently delete a document. Tier: approval.",
	input_schema={
		"type": "object",
		"properties": {"doctype": {"type": "string"}, "name": {"type": "string"}},
		"required": ["doctype", "name"],
		"additionalProperties": False,
	},
	tier="approval",
)
def delete_document(doctype: str, name: str) -> dict:
	doc = _load_for_write(doctype, name)
	doc.check_permission("delete")
	frappe.delete_doc(doctype, name)
	return {"doctype": doctype, "name": name, "deleted": True}


def _load_for_write(doctype: str, name: str):
	if not frappe.db.exists(doctype, name):
		raise ToolError(_("{0} {1} not found.").format(doctype, name))
	doc = frappe.get_doc(doctype, name)
	if scoping.has_company_field(doctype) and doc.get("company"):
		scoping.assert_company_allowed(doc.company)
	return doc
```

- [ ] **Step 5: Implement methods.py**

Replace `erpnext/ai/tools/methods.py` with:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _

from erpnext.ai.registry import ToolError, settings, tool


def echo(value: str) -> str:
	"""Trivial allowlistable method, used to prove the allowlist works."""
	return value


@tool(
	name="call_method",
	description=(
		"Call a specific ERPNext server method by dotted path. Only paths explicitly listed in "
		"AI Settings are permitted. Tier: approval."
	),
	input_schema={
		"type": "object",
		"properties": {"method": {"type": "string"}, "args": {"type": "object"}},
		"required": ["method"],
		"additionalProperties": False,
	},
	tier="approval",
)
def call_method(method: str, args: dict | None = None) -> object:
	raw = (settings().allowed_methods or "[]").strip() or "[]"
	try:
		allowed = json.loads(raw)
	except ValueError:
		allowed = []

	if method not in allowed:
		raise ToolError(
			_("Method {0} is not in the AI Settings allowlist.").format(method)
		)

	return frappe.get_attr(method)(**(args or {}))
```

- [ ] **Step 6: Run the tests**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend bench --site erp.localhost migrate
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_write_tools
```

Expected: PASS, 7 tests.

- [ ] **Step 7: Run the whole AI suite**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests
```

Expected: PASS, all tests.

- [ ] **Step 8: Commit**

```bash
git add erpnext/ai
git commit -m "feat(ai): write tools with idempotency, value caps and method allowlist"
```

---

### Task 8: Paperclip client

**Files:**
- Create: `erpnext/ai/paperclip.py`
- Test: `erpnext/ai/tests/test_paperclip.py`

**Interfaces:**
- Consumes: `AI Settings`.
- Produces:
  - `PaperclipError(Exception)`
  - `get_client() -> PaperclipClient`
  - `PaperclipClient.health() -> dict`, `.request(method: str, path: str, json_body: dict | None = None, params: dict | None = None) -> dict`
  - Whitelisted proxies, all role-gated: `send_message(message: str) -> dict`, `get_thread() -> dict`, `get_run_events(run_id: str, after_seq: int = 0) -> dict`, `list_approvals() -> dict`, `resolve_approval(action_request_id: str, approve: bool) -> dict`
  - `assert_ai_user() -> None`

- [ ] **Step 1: Write the failing test**

Create `erpnext/ai/tests/test_paperclip.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.ai import paperclip, registry


class TestPaperclip(IntegrationTestCase):
	def setUp(self):
		registry.settings.cache_clear()
		doc = frappe.get_single("AI Settings")
		doc.enabled = 1
		doc.paperclip_url = "https://paperclip.example.com"
		doc.paperclip_company_id = "c-1"
		doc.agent_id = "a-1"
		doc.board_api_key = "secret"
		doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
		doc.allowed_roles = '["System Manager"]'
		doc.save()
		registry.settings.cache_clear()

	def tearDown(self):
		registry.settings.cache_clear()
		frappe.db.rollback()

	def test_client_sends_bearer_token(self):
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 200
			req.return_value.json.return_value = {"status": "ok"}
			paperclip.get_client().health()
		headers = req.call_args.kwargs["headers"]
		self.assertEqual(headers["Authorization"], "Bearer secret")

	def test_non_2xx_raises(self):
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 403
			req.return_value.text = "nope"
			with self.assertRaises(paperclip.PaperclipError):
				paperclip.get_client().health()

	def test_url_is_joined_without_double_slash(self):
		with patch("erpnext.ai.paperclip.requests.request") as req:
			req.return_value.status_code = 200
			req.return_value.json.return_value = {}
			paperclip.get_client().request("GET", "/api/health")
		self.assertEqual(req.call_args.args[1], "https://paperclip.example.com/api/health")

	def test_role_gate_blocks_users_without_an_allowed_role(self):
		with patch("erpnext.ai.paperclip.frappe.get_roles", return_value=["Stock User"]):
			self.assertRaises(frappe.PermissionError, paperclip.assert_ai_user)

	def test_role_gate_allows_listed_role(self):
		with patch("erpnext.ai.paperclip.frappe.get_roles", return_value=["System Manager"]):
			paperclip.assert_ai_user()
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_paperclip
```

Expected: FAIL — `No module named 'erpnext.ai.paperclip'`.

- [ ] **Step 3: Implement the client**

Create `erpnext/ai/paperclip.py`:

```python
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

	def request(self, method: str, path: str, json_body: dict | None = None, params: dict | None = None) -> dict:
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
		"action_requests": client.request(
			"GET", f"/api/companies/{client.company_id}/tools/action-requests"
		)
	}


@frappe.whitelist(methods=["POST"])
def resolve_approval(action_request_id: str, approve: bool) -> dict:
	assert_ai_user()
	verb = "approve" if approve else "decline"
	get_client().request("POST", f"/api/tool-gateway/action-requests/{action_request_id}/{verb}")
	return {"id": action_request_id, "resolved": verb}
```

- [ ] **Step 4: Run the tests**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_paperclip
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add erpnext/ai/paperclip.py erpnext/ai/tests/test_paperclip.py
git commit -m "feat(ai): server-side Paperclip client with role-gated proxies"
```

---

### Task 9: Workspace nav entry and desk page shell

**Files:**
- Create: `erpnext/ai/workspace/ai/ai.json`
- Create: `erpnext/ai/page/__init__.py`, `erpnext/ai/page/ai/__init__.py`
- Create: `erpnext/ai/page/ai/ai.json`, `erpnext/ai/page/ai/ai.js`, `erpnext/ai/page/ai/ai.py`
- Test: `erpnext/ai/tests/test_workspace.py`

**Interfaces:**
- Consumes: `paperclip.assert_ai_user`.
- Produces: Workspace `AI` at `sequence_id 1.5`; desk Page `ai` at `/app/ai`; global `mountAI(element)` supplied by the Task 10 bundle.

- [ ] **Step 1: Write the failing test**

Create `erpnext/ai/tests/test_workspace.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestAIWorkspace(IntegrationTestCase):
	def test_workspace_sits_between_home_and_invoicing(self):
		ai = frappe.db.get_value("Workspace", "AI", ["sequence_id", "public", "type"], as_dict=True)
		home = frappe.db.get_value("Workspace", "Home", "sequence_id")
		invoicing = frappe.db.get_value("Workspace", "Invoicing", "sequence_id")
		self.assertTrue(home < ai.sequence_id < invoicing)
		self.assertTrue(ai.public)

	def test_workspace_links_to_the_ai_page(self):
		ws = frappe.get_doc("Workspace", "AI")
		self.assertEqual(ws.type, "Link")
		self.assertEqual(ws.link_type, "Page")
		self.assertEqual(ws.link_to, "ai")

	def test_page_exists(self):
		self.assertTrue(frappe.db.exists("Page", "ai"))
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_workspace
```

Expected: FAIL — workspace `AI` does not exist.

- [ ] **Step 3: Create the workspace fixture**

```bash
mkdir -p erpnext/ai/workspace/ai
touch erpnext/ai/workspace/__init__.py erpnext/ai/workspace/ai/__init__.py
```

Create `erpnext/ai/workspace/ai/ai.json`:

```json
{
 "app": "erpnext",
 "charts": [],
 "content": "[]",
 "creation": "2026-08-06 12:00:00.000000",
 "custom_blocks": [],
 "docstatus": 0,
 "doctype": "Workspace",
 "external_link": "",
 "for_user": "",
 "hide_custom": 0,
 "icon": "sparkles",
 "idx": 0,
 "is_hidden": 0,
 "label": "AI",
 "link_to": "ai",
 "link_type": "Page",
 "links": [],
 "modified": "2026-08-06 12:00:00.000000",
 "modified_by": "Administrator",
 "module": "AI",
 "number_cards": [],
 "owner": "Administrator",
 "parent_page": "",
 "public": 1,
 "quick_lists": [],
 "roles": [],
 "sequence_id": 1.5,
 "shortcuts": [],
 "title": "AI",
 "type": "Link"
}
```

`sequence_id 1.5` is what places the icon between Home (`1.0`) and Invoicing (`2.0`) in the desk rail. Do not renumber the neighbours.

- [ ] **Step 4: Create the desk page**

```bash
mkdir -p erpnext/ai/page/ai
touch erpnext/ai/page/__init__.py erpnext/ai/page/ai/__init__.py
```

Create `erpnext/ai/page/ai/ai.json`:

```json
{
 "content": null,
 "creation": "2026-08-06 12:00:00.000000",
 "docstatus": 0,
 "doctype": "Page",
 "idx": 0,
 "modified": "2026-08-06 12:00:00.000000",
 "modified_by": "Administrator",
 "module": "AI",
 "name": "ai",
 "owner": "Administrator",
 "page_name": "ai",
 "roles": [{"role": "System Manager"}],
 "script": null,
 "standard": "Yes",
 "style": null,
 "system_page": 0,
 "title": "AI"
}
```

Create `erpnext/ai/page/ai/ai.py`:

```python
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
```

Create `erpnext/ai/page/ai/ai.js`:

```javascript
frappe.pages["ai"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("AI"),
		single_column: true,
	});

	const container = $('<div class="erpnext-ai-root"></div>').appendTo(
		$(wrapper).find(".layout-main-section")
	)[0];

	// The SPA is built as an IIFE exposing window.mountAI so it can live inside the
	// desk shell — which is the whole point, since the AI entry is a rail icon and
	// navigating away from the desk would lose the rail.
	frappe.require("/assets/erpnext/ai/ai.bundle.js", () => {
		if (window.mountAI) {
			window.mountAI(container);
		} else {
			container.innerText = __("AI bundle failed to load. Run: yarn build:ai");
		}
	});
};
```

- [ ] **Step 5: Migrate and run the tests**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend bench --site erp.localhost migrate
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_workspace
```

Expected: PASS, 3 tests.

- [ ] **Step 6: Verify the icon appears in the rail**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost console <<'PY'
import frappe
rows = frappe.get_all("Workspace", filters={"public": 1}, fields=["name", "sequence_id"], order_by="sequence_id")
print([(r.name, r.sequence_id) for r in rows][:5])
PY
```

Expected: `AI` appears between `Home` and `Invoicing`.

- [ ] **Step 7: Commit**

```bash
git add erpnext/ai/workspace erpnext/ai/page erpnext/ai/tests/test_workspace.py
git commit -m "feat(ai): AI workspace rail entry and desk page shell"
```

---

### Task 10: React SPA

**Files:**
- Create: `ai/package.json`, `ai/vite.config.ts`, `ai/tsconfig.json`, `ai/index.html`
- Create: `ai/src/main.tsx`, `ai/src/App.tsx`, `ai/src/api.ts`, `ai/src/types.ts`
- Create: `ai/src/components/Thread.tsx`, `ai/src/components/RunFeed.tsx`, `ai/src/components/ApprovalCard.tsx`
- Modify: `package.json` (root, add `build:ai`)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: whitelisted methods from Task 8 and `get_boot_info` from Task 9.
- Produces: `window.mountAI(element: HTMLElement): void`, bundle at `erpnext/public/ai/ai.bundle.js`.

- [ ] **Step 1: Scaffold the package**

Create `ai/package.json`:

```json
{
  "name": "erpnext-ai",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "lint": "eslint ."
  },
  "dependencies": {
    "react": "^19.2.7",
    "react-dom": "^19.2.7",
    "react-markdown": "^10.1.0",
    "remark-gfm": "^4.0.1",
    "lucide-react": "^1.14.0"
  },
  "devDependencies": {
    "@types/react": "^19.2.7",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.3",
    "typescript": "~5.9.3",
    "vite": "^8.0.16"
  }
}
```

Create `ai/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "skipLibCheck": true,
    "noEmit": true,
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  },
  "include": ["src"]
}
```

Create `ai/index.html` (used only by `yarn dev`):

```html
<!doctype html>
<html lang="en">
  <head><meta charset="UTF-8" /><title>ERPNext AI</title></head>
  <body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body>
</html>
```

- [ ] **Step 2: Configure Vite for IIFE output**

Create `ai/vite.config.ts`:

```typescript
import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Library/IIFE build rather than an app build: the bundle is loaded INTO a Frappe
// desk page via frappe.require, so it must expose a single global mount function
// and must not assume it owns the document.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  build: {
    outDir: '../erpnext/public/ai',
    emptyOutDir: true,
    target: 'es2020',
    lib: {
      entry: path.resolve(__dirname, 'src/main.tsx'),
      name: 'ERPNextAI',
      formats: ['iife'],
      fileName: () => 'ai.bundle.js',
    },
    rollupOptions: {
      output: { assetFileNames: 'ai.bundle.[ext]' },
    },
  },
});
```

- [ ] **Step 3: Write the API layer**

Create `ai/src/types.ts`:

```typescript
export type Comment = {
  id: string;
  body: string;
  authorType?: string;
  agentId?: string | null;
  createdAt: string;
};

export type RunEvent = {
  seq: number;
  eventType: string;
  message?: string | null;
  payload?: Record<string, unknown>;
};

export type ActionRequest = {
  id: string;
  toolName?: string;
  summary?: string;
  createdAt?: string;
};

export type Thread = {
  issue_id: string;
  comments: Comment[];
  live_runs: Array<{ id: string; status: string }>;
};
```

Create `ai/src/api.ts`:

```typescript
import type { ActionRequest, RunEvent, Thread } from './types';

// Every Paperclip call is proxied through ERPNext so the board API key never
// reaches the browser and each entry point re-checks the caller's role.
async function call<T>(method: string, args: Record<string, unknown> = {}, post = false): Promise<T> {
  const url = `/api/method/${method}`;
  const csrf = (window as any).frappe?.csrf_token ?? '';
  const res = post
    ? await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': csrf },
        body: JSON.stringify(args),
      })
    : await fetch(`${url}?${new URLSearchParams(args as Record<string, string>)}`, {
        headers: { 'X-Frappe-CSRF-Token': csrf },
      });

  if (!res.ok) throw new Error(`${method} failed (${res.status})`);
  return (await res.json()).message as T;
}

export const api = {
  boot: () => call<{ enabled: boolean; company: string; agent_id: string }>('erpnext.ai.page.ai.ai.get_boot_info'),
  thread: () => call<Thread>('erpnext.ai.paperclip.get_thread'),
  send: (message: string) => call<{ issue_id: string }>('erpnext.ai.paperclip.send_message', { message }, true),
  events: (runId: string, afterSeq: number) =>
    call<{ events: RunEvent[] }>('erpnext.ai.paperclip.get_run_events', {
      run_id: runId,
      after_seq: String(afterSeq),
    }),
  approvals: () => call<{ action_requests: ActionRequest[] }>('erpnext.ai.paperclip.list_approvals'),
  resolve: (id: string, approve: boolean) =>
    call<{ resolved: string }>('erpnext.ai.paperclip.resolve_approval', { action_request_id: id, approve }, true),
};
```

- [ ] **Step 4: Write the run feed**

Create `ai/src/components/RunFeed.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { api } from '../api';
import type { RunEvent } from '../types';

// Runs expose no SSE, so we poll the cursor-based event feed. Showing the agent's
// actual steps is what makes a 40-second reply read as work rather than a hang.
const LABELS: Record<string, string> = {
  'adapter.invoke': 'Thinking',
  status: 'Status',
  chunk: '',
  call_completed: 'Used a tool',
  call_denied: 'Tool call denied',
  call_failed: 'Tool call failed',
  approval_requested: 'Waiting for your approval',
  approval_resolved: 'Approval resolved',
  rate_limited: 'Rate limited',
  error: 'Error',
};

export function RunFeed({ runId }: { runId: string }) {
  const [events, setEvents] = useState<RunEvent[]>([]);

  useEffect(() => {
    let seq = 0;
    let cancelled = false;

    const tick = async () => {
      if (cancelled) return;
      try {
        const { events: fresh } = await api.events(runId, seq);
        if (fresh?.length) {
          seq = Math.max(...fresh.map((e) => e.seq ?? seq));
          setEvents((prev) => [...prev, ...fresh]);
        }
      } catch {
        // Transient failures are expected while a run starts; keep polling.
      }
      if (!cancelled) window.setTimeout(tick, 1000);
    };

    tick();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (!events.length) return <div className="text-muted">Waking the CEO…</div>;

  return (
    <ul className="ai-run-feed">
      {events.map((e, i) => (
        <li key={`${e.seq}-${i}`}>
          <strong>{LABELS[e.eventType] ?? e.eventType}</strong>
          {e.message ? ` — ${e.message}` : ''}
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 5: Write the approval card**

Create `ai/src/components/ApprovalCard.tsx`:

```tsx
import { useState } from 'react';
import { api } from '../api';
import type { ActionRequest } from '../types';

export function ApprovalCard({ request, onResolved }: { request: ActionRequest; onResolved: () => void }) {
  const [busy, setBusy] = useState(false);

  const resolve = async (approve: boolean) => {
    setBusy(true);
    try {
      await api.resolve(request.id, approve);
      onResolved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ai-approval">
      <div className="ai-approval-title">
        Approval needed: <code>{request.toolName ?? 'tool call'}</code>
      </div>
      {request.summary ? <p>{request.summary}</p> : null}
      <div className="ai-approval-actions">
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => resolve(true)}>
          Approve
        </button>
        <button className="btn btn-default btn-sm" disabled={busy} onClick={() => resolve(false)}>
          Decline
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Write the thread and app shell**

Create `ai/src/components/Thread.tsx`:

```tsx
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Comment } from '../types';

export function Thread({ comments }: { comments: Comment[] }) {
  return (
    <div className="ai-thread">
      {comments.map((c) => (
        <div key={c.id} className={`ai-turn ai-turn-${c.agentId ? 'agent' : 'user'}`}>
          <div className="ai-turn-author">{c.agentId ? 'CEO' : 'You'}</div>
          <Markdown remarkPlugins={[remarkGfm]}>{c.body}</Markdown>
        </div>
      ))}
    </div>
  );
}
```

Create `ai/src/App.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { ApprovalCard } from './components/ApprovalCard';
import { RunFeed } from './components/RunFeed';
import { Thread } from './components/Thread';
import type { ActionRequest, Thread as ThreadType } from './types';

export function App() {
  const [thread, setThread] = useState<ThreadType | null>(null);
  const [approvals, setApprovals] = useState<ActionRequest[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [t, a] = await Promise.all([api.thread(), api.approvals()]);
      setThread(t);
      setApprovals(a.action_requests ?? []);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 5000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const send = async () => {
    const message = draft.trim();
    if (!message) return;
    setDraft('');
    await api.send(message);
    refresh();
  };

  if (error) return <div className="ai-error">Paperclip unavailable: {error}</div>;
  if (!thread) return <div className="text-muted">Loading…</div>;

  const activeRun = thread.live_runs?.[0];

  return (
    <div className="ai-app">
      <Thread comments={thread.comments ?? []} />
      {activeRun ? <RunFeed runId={activeRun.id} /> : null}
      {approvals.map((r) => (
        <ApprovalCard key={r.id} request={r} onResolved={refresh} />
      ))}
      <div className="ai-composer">
        <textarea
          value={draft}
          rows={3}
          placeholder="Ask your CEO…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send();
          }}
        />
        <button className="btn btn-primary" onClick={send}>
          Send
        </button>
      </div>
    </div>
  );
}
```

Create `ai/src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';

// Exposed as a global because the bundle is mounted into a Frappe desk page
// rather than owning its own document.
export function mountAI(element: HTMLElement) {
  createRoot(element).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
}

(window as unknown as { mountAI: typeof mountAI }).mountAI = mountAI;
```

- [ ] **Step 7: Wire the root build script and ignore build output**

Add to the root `package.json` `scripts` block:

```json
"build:ai": "cd ai && yarn install && yarn build"
```

Append to `.gitignore` (the file already ends with a newline — verified):

```
erpnext/public/ai
```

- [ ] **Step 8: Build and verify the bundle**

```bash
cd /mnt/projects/erpnext && yarn build:ai
ls -l erpnext/public/ai/ai.bundle.js
grep -c "mountAI" erpnext/public/ai/ai.bundle.js
```

Expected: the bundle exists and `mountAI` appears at least once.

- [ ] **Step 9: Commit**

```bash
git add ai package.json .gitignore
git commit -m "feat(ai): React desk SPA with run-event feed and inline approvals"
```

---

### Task 11: Paperclip wiring, docs and the gateway invariant check

**Files:**
- Create: `prod-docker/connect-paperclip.sh`
- Create: `erpnext/ai/README.md`
- Test: `erpnext/ai/tests/test_settings_sync.py`

**Interfaces:**
- Consumes: `PAPERCLIP_*` env keys (already in `.env` and `.env.example`).
- Produces: a repeatable script registering ERPNext as a Paperclip `mcp_remote` tool connection, and a documented invariant check.

- [ ] **Step 1: Write the failing test**

Create `erpnext/ai/tests/test_settings_sync.py`:

```python
# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import pathlib
import re
import unittest

from frappe.tests import UnitTestCase

# In production the app is copied into the bench without the surrounding repo,
# so prod-docker/ is absent there. Skip rather than fail: this guards the repo,
# and it runs wherever the repo IS present (dev bench, CI, pre-commit).
EXAMPLE = pathlib.Path(__file__).resolve().parents[3] / "prod-docker" / ".env.example"

EXPECTED_KEYS = {
	"PAPERCLIP_URL",
	"PAPERCLIP_COMPANY_ID",
	"PAPERCLIP_AGENT_ID",
	"PAPERCLIP_BOARD_API_KEY",
	"ERPNEXT_MCP_URL",
}


@unittest.skipUnless(EXAMPLE.exists(), "prod-docker/.env.example not present in this checkout")
class TestEnvParity(UnitTestCase):
	def test_env_example_declares_every_paperclip_key(self):
		declared = set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", EXAMPLE.read_text(), re.M))
		self.assertTrue(
			EXPECTED_KEYS <= declared, f"missing from .env.example: {EXPECTED_KEYS - declared}"
		)

	def test_env_example_holds_no_secret_value(self):
		# re.M so ^...$ anchors to the line, not the whole file.
		self.assertRegex(EXAMPLE.read_text(), r"(?m)^PAPERCLIP_BOARD_API_KEY=\s*$")
```

- [ ] **Step 2: Run it and confirm it passes or fails honestly**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests.test_settings_sync
```

Expected: PASS (the keys were added during design). If it fails, fix `.env.example` — never commit a real value there.

- [ ] **Step 3: Write the connection script**

Create `prod-docker/connect-paperclip.sh`:

```bash
#!/bin/bash
# Register ERPNext as a Paperclip tool connection and push PAPERCLIP_* into AI Settings.
#
# IMPORTANT: ERPNext must be reachable ONLY through Paperclip's tool gateway. The CEO
# agent runs with dangerouslySkipPermissions, so adding this MCP server to its local
# Claude Code config would bypass every approval control in the design.
set -euo pipefail
cd "$(dirname "$0")"

set -a
source ./.env
set +a

: "${PAPERCLIP_URL:?blank in .env}"
: "${PAPERCLIP_COMPANY_ID:?blank in .env}"
: "${PAPERCLIP_AGENT_ID:?blank in .env}"
: "${PAPERCLIP_BOARD_API_KEY:?blank in .env}"
: "${ERPNEXT_MCP_URL:?set ERPNEXT_MCP_URL to the LAN URL Paperclip should dial, e.g. http://192.168.10.118:4410}"
: "${ERPNEXT_API_KEY:?ERPNext API key for the AI service user}"
: "${ERPNEXT_API_SECRET:?ERPNext API secret for the AI service user}"

echo "Registering ERPNext as an mcp_remote tool connection…"
curl -sS -X POST \
  -H "Authorization: Bearer $PAPERCLIP_BOARD_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{
        \"name\": \"ERPNext\",
        \"applicationName\": \"ERPNext\",
        \"transport\": \"mcp_remote\",
        \"authKind\": \"api_key\",
        \"connectionKind\": \"managed\",
        \"status\": \"active\",
        \"enabled\": true,
        \"transportConfig\": {
          \"url\": \"${ERPNEXT_MCP_URL}/api/method/erpnext.ai.mcp.handle\",
          \"headers\": {\"Authorization\": \"token ${ERPNEXT_API_KEY}:${ERPNEXT_API_SECRET}\"}
        }
      }" \
  "$PAPERCLIP_URL/api/companies/$PAPERCLIP_COMPANY_ID/tools/connections"
echo

echo "Pushing settings into the site…"
docker compose exec -T \
  -e PC_URL="$PAPERCLIP_URL" \
  -e PC_COMPANY="$PAPERCLIP_COMPANY_ID" \
  -e PC_AGENT="$PAPERCLIP_AGENT_ID" \
  -e PC_KEY="$PAPERCLIP_BOARD_API_KEY" \
  backend bench --site "${SITE_NAME:-erp.localhost}" console <<'PYEOF'
import os

import frappe

doc = frappe.get_doc("AI Settings")
doc.paperclip_url = os.environ["PC_URL"]
doc.paperclip_company_id = os.environ["PC_COMPANY"]
doc.agent_id = os.environ["PC_AGENT"]
doc.board_api_key = os.environ["PC_KEY"]
if not doc.erpnext_company:
    doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
doc.enabled = 1
doc.save()
frappe.db.commit()
print("AI Settings saved for", frappe.local.site, "company", doc.erpnext_company)
PYEOF

echo
echo "Next: create a tool policy in Paperclip requiring approval for"
echo "  submit_document, cancel_document, delete_document, call_method"
echo "and any update_document on a submitted record. Verify with tools/policy/test."
```

```bash
chmod +x prod-docker/connect-paperclip.sh
```

- [ ] **Step 4: Document the invariant**

Create `erpnext/ai/README.md`:

```markdown
# ERPNext AI

ERPNext exposed as an MCP tool server for a Paperclip CEO agent, plus the desk `AI` tab.

- Spec: `docs/superpowers/specs/2026-08-06-erpnext-ai-cofounder-design.md`
- Roadmap (RAG index, proactive routines, multi-user): `docs/superpowers/specs/2026-08-06-erpnext-ai-roadmap.md`

## The one rule

**ERPNext must be reachable only through Paperclip's tool gateway.**

The CEO agent runs with `dangerouslySkipPermissions: true`, so Claude Code
auto-approves its own tool calls. Human approval exists solely because governed
calls traverse the gateway, which suspends them into action requests. Adding
this MCP server to any agent's local Claude Code MCP config silently bypasses
every approval control here — it is a defect, not a shortcut.

The caps in `AI Settings` (`enabled_tools`, `max_batch_size`,
`max_document_value`, `allowed_methods`) exist because that invariant can be
broken by configuration outside this repository.

## Layout

| File | Responsibility |
|---|---|
| `mcp.py` | JSON-RPC transport (`initialize`, `tools/list`, `tools/call`) |
| `registry.py` | Tool registration, allowlist, blast-radius caps |
| `scoping.py` | Bound-company resolution; refuses out-of-scope entities |
| `knowledge.py` | Live orientation text and the workspace/URL map |
| `tools/` | The fourteen tools |
| `paperclip.py` | Role-gated server-side proxies for the SPA |

## Setup

1. Fill `PAPERCLIP_*` in `prod-docker/.env`.
2. Create an ERPNext API key/secret for the AI service user.
3. `ERPNEXT_MCP_URL=http://<lan-ip>:4410 ./prod-docker/connect-paperclip.sh`
4. In Paperclip, bind the connection's catalogue to the CEO via a tool access
   profile, and add a tool policy requiring approval for `submit_document`,
   `cancel_document`, `delete_document` and `call_method`.

## Tests

```bash
bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests
```
```

- [ ] **Step 5: Run the full suite**

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext --module erpnext.ai.tests
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site erp.localhost run-tests --app erpnext \
  --module erpnext.ai.doctype.ai_settings.test_ai_settings
```

Expected: PASS across all AI tests.

- [ ] **Step 6: Lint**

```bash
cd /mnt/projects/erpnext
ruff check erpnext/ai
ruff format --check erpnext/ai
```

Expected: no errors. Run `ruff format erpnext/ai` if formatting differs.

- [ ] **Step 7: Commit**

```bash
git add prod-docker/connect-paperclip.sh erpnext/ai/README.md erpnext/ai/tests/test_settings_sync.py
git commit -m "feat(ai): Paperclip connection script, docs and gateway invariant"
```

---

## Manual verification (after Task 11)

These need the live Paperclip instance and cannot be unit-tested.

- [ ] **MCP handshake reaches ERPNext.** From the Paperclip host:
  `curl -X POST -H "Authorization: token KEY:SECRET" -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' http://<lan-ip>:4410/api/method/erpnext.ai.mcp.handle`
  Expect `protocolVersion` and orientation text naming the company.
- [ ] **Catalogue populates.** `GET /api/tool-connections/{id}/catalog` lists all fourteen tools.
- [ ] **Free tier runs unattended.** Ask the CEO "what is my company context" and confirm it answers without an approval prompt.
- [ ] **Approval tier actually pauses.** Ask it to submit a draft; confirm an action request appears in the desk tab and the call only completes after Approve.
- [ ] **Refusal is visible.** Ask for a company outside `erpnext_company`; confirm a clear refusal rather than silently different data.
- [ ] **Idempotency holds.** Call `create_document` twice with the same key; confirm one document and `reused: true`.
- [ ] **The invariant is not violated.** Confirm `erpnext` does NOT appear in the CEO's local Claude Code MCP config on the Mac.

## Deferred to later specs

Tracked in the roadmap, deliberately not built here: the pgvector RAG index (spec 2), the five proactive routines and briefing view (spec 3), multi-user signed identity passthrough, and enabling the CEO's heartbeat (needed only for routines, not chat).
