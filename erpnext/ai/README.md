# ERPNext AI

ERPNext exposed as an MCP tool server for a Paperclip CEO agent, plus the desk `AI` tab.

- Spec: `docs/superpowers/specs/2026-08-06-erpnext-ai-cofounder-design.md`
- Roadmap (RAG index, proactive routines, multi-user): `docs/superpowers/specs/2026-08-06-erpnext-ai-roadmap.md`

## The one rule

**ERPNext must be reachable only through Paperclip's tool gateway.**

Human approval of the agent's privileged actions exists *only* because tool calls
traverse Paperclip's tool gateway, which suspends governed calls into approval
requests. The live CEO agent runs with `dangerouslySkipPermissions: true`, meaning
its Claude Code runtime auto-approves its own tool calls with no prompt.

Therefore: if this MCP server were ever added directly to an agent's local MCP
config, every approval control in this design would be silently bypassed -- it
would submit invoices, cancel documents and delete records with no human in the
loop, and nothing would appear to be wrong. Adding it locally is a defect, not a
shortcut.

The caps in `AI Settings` (`enabled_tools`, `max_batch_size`,
`max_document_value`, `allowed_methods`) exist precisely because that invariant
can be broken by configuration outside this repository.

## Layout

| File | Responsibility |
|---|---|
| `mcp.py` | JSON-RPC transport (`initialize`, `tools/list`, `tools/call`), served as the single whitelisted endpoint `erpnext.ai.mcp.handle` |
| `registry.py` | Tool registration (`@tool`), the `enabled_tools` allowlist, and the blast-radius caps (`clamp_limit`, `assert_value_within_cap`) |
| `scoping.py` | Bound-company resolution and filter injection; refuses out-of-scope entities and doctypes that cannot be company-scoped |
| `knowledge.py` | Live orientation text handed to the agent on `initialize`, and the workspace/URL map used by discovery tools |
| `paperclip.py` | Role-gated server-side proxies for the desk SPA (thread, run events, approvals) -- the board API key never reaches the browser |
| `tools/discovery.py` | `get_company_context`, `search_doctypes`, `describe_doctype`, `list_reports`, `get_workspace_map` |
| `tools/documents.py` | `search_documents`, `get_document`, `create_document`, `update_document`, `submit_document`, `cancel_document`, `delete_document` |
| `tools/reports.py` | `run_report` |
| `tools/methods.py` | `call_method`, gated by the `allowed_methods` allowlist |
| `doctype/ai_settings/` | The `AI Settings` single: Paperclip connection details, `erpnext_company` binding, and every cap above |
| `doctype/ai_company_item/` | Child table of `AI Settings.additional_companies` -- companies beyond the primary that the agent may also access |
| `doctype/ai_idempotency_record/` | Backing store for `create_document`'s idempotency key, so a retried call reuses the original document instead of creating a duplicate |
| `page/ai/` | Desk page hosting the SPA; `ai.py::get_boot_info` gates entry by `allowed_roles` |
| `workspace/ai/` | The `AI` workspace entry linking to the page |
| `ai/` (repo root) | React source for the desk SPA; `vite build` outputs an IIFE bundle to `erpnext/public/ai/ai.bundle.js`, loaded into the desk page via `frappe.require` |

That is fifteen tools in total across `discovery` (5), `documents` (7), `reports` (1), `methods` (1) and `ping` (registered directly in `registry.py`).

## Setup

1. Build the desk SPA: `yarn build:ai` (from the repo root). This installs
   the `ai/` frontend's dependencies and runs `vite build`, which outputs
   `erpnext/public/ai/ai.bundle.js` -- `public/ai` is gitignored, so a fresh
   checkout has no bundle until this runs, and the desk page will otherwise
   fail to load with "AI bundle failed to load".
2. Fill `PAPERCLIP_*` and `ERPNEXT_MCP_URL` in `prod-docker/.env` (see
   `prod-docker/.env.example` for the full key set).
3. Create an ERPNext API key/secret for the AI service user. Export them for
   the next step only -- they are not stored in `.env` (see the comment in
   `connect-paperclip.sh`).
4. `ERPNEXT_API_KEY=... ERPNEXT_API_SECRET=... ./prod-docker/connect-paperclip.sh`
5. In Paperclip, bind the connection's catalogue to the CEO via a tool access
   profile, and add a tool policy requiring approval for `submit_document`,
   `cancel_document`, `delete_document` and `call_method`.

## Tests

`bench run-tests --module erpnext.ai.tests` silently discovers **zero** tests --
it names a package, not a module, and `bench` does not walk into it. Test
modules must be enumerated explicitly:

```bash
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site test.localhost run-tests --app erpnext --module erpnext.ai.tests.test_settings_sync
docker compose -f prod-docker/compose.yaml exec -T backend \
  bench --site test.localhost run-tests --app erpnext \
  --module erpnext.ai.doctype.ai_settings.test_ai_settings
```

`.superpowers/sdd/run-all-tests.sh` does this enumeration for the whole suite
(every `erpnext/ai/tests/test_*.py` plus the `ai_settings` doctype test) and is
the fastest way to check nothing regressed. Never point either script at
`erp.localhost` -- that is the production site with real books; both scripts
run against the isolated `test.localhost` site instead.

## Company scoping fails closed on multi-company sites

`scoping.py` can only enforce a company boundary on doctypes that carry a
`company` field, and cannot enforce one inside a report at all (`run_report`
executes arbitrary report logic that may join across companies). On a site
with exactly one Company this is moot -- there is nothing to leak between. The
moment a second Company exists (`scoping.is_multi_company_site()`), every
company-less doctype not on the explicit safe list and every `run_report` call
refuses outright, rather than silently returning unscoped data. Single-company
sites get full capability; multi-company sites lose report access and access
to a handful of company-less doctypes until those are individually vetted.

## Deferred to later specs

Tracked in the roadmap, deliberately not built here: the pgvector RAG index
(spec 2), the five proactive routines and briefing view (spec 3), multi-user
signed identity passthrough, and enabling the CEO's heartbeat (needed only for
routines, not chat).
