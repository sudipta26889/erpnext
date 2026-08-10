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
| `paperclip.py` | Role-gated server-side proxies for the desk SPA (board chat, thread, run events, approvals) -- the board API key never reaches the browser |
| `tools/discovery.py` | `get_company_context`, `search_doctypes`, `describe_doctype`, `list_reports`, `get_workspace_map` |
| `tools/documents.py` | `search_documents`, `get_document`, `create_document`, `update_document`, `submit_document`, `cancel_document`, `delete_document` |
| `tools/reports.py` | `run_report` |
| `tools/methods.py` | `call_method`, gated by the `allowed_methods` allowlist |
| `doctype/ai_settings/` | The `AI Settings` single: Paperclip connection details, `erpnext_company` binding, and every cap above |
| `doctype/ai_company_item/` | Child table of `AI Settings.additional_companies` -- companies beyond the primary that the agent may also access |
| `doctype/ai_idempotency_record/` | Backing store for `create_document`'s idempotency key, so a retried call reuses the original document instead of creating a duplicate |
| `page/ai_chat/` | Desk page hosting the SPA at `/desk/ai-chat`; `ai_chat.py::get_boot_info` gates entry by `allowed_roles` |
| `workspace/ai/` | The `AI` workspace entry linking to the page |
| `ai/` (repo root) | React source for the desk SPA; `vite build` outputs an IIFE bundle plus its stylesheet to `erpnext/public/ai/`, both loaded by the desk page via `frappe.require`. `ai/test/smoke.mjs` runs the built bundle in jsdom -- a window with no `process` -- and is what catches a bundle that cannot start in a browser |

That is fifteen tools in total across `discovery` (5), `documents` (7), `reports` (1), `methods` (1) and `ping` (registered directly in `registry.py`).

## Setup

1. Build the desk SPA: `yarn build:ai` (from the repo root). This installs
   the `ai/` frontend's dependencies and runs `vite build`, which outputs
   `erpnext/public/ai/ai.js` and `ai.css`.

   **That output is committed**, unlike every other built asset here. The
   production image is built from this repo over git and its only asset step is
   `bench build`, which is Frappe's esbuild over `public/js/*.bundle.js` -- it
   knows nothing about `ai/`'s Vite build, so a gitignored bundle would simply
   be missing in production and the tab would render "AI bundle failed to load".
   Consequence: **any change under `ai/src/` must be followed by `yarn build:ai`
   and the rebuilt bundle committed alongside it**, or production silently keeps
   serving the previous UI.
2. Fill `PAPERCLIP_*` and `ERPNEXT_MCP_URL` in `prod-docker/.env` (see
   `prod-docker/.env.example` for the full key set).
3. Create an ERPNext API key/secret for the AI service user. Export them for
   the next step only -- they are not stored in `.env` (see the comment in
   `connect-paperclip.sh`). **That user must also hold a role listed in
   AI Settings -> Allowed Roles** (`allowed_roles`, a JSON list) -- checked by
   `paperclip.assert_ai_user()` at the top of every MCP call, including
   `initialize`. The shipped default is `["System Manager"]`. A service user
   created for least privilege (no System Manager role) gets `AI_FORBIDDEN`
   on every call, including the handshake, with nothing else pointing at AI
   Settings -- either grant the service user an already-listed role, or add
   its role to `allowed_roles` first. `allowed_roles` is shared with the desk
   `AI` page's own gate (`page/ai_chat/ai_chat.py::get_boot_info`), so admitting the
   service account's role here also grants that role the desk AI tab for any
   human user who holds it -- pick or add a role with that in mind, not just
   "whatever the service user already has".
4. `ERPNEXT_API_KEY=... ERPNEXT_API_SECRET=... ./prod-docker/connect-paperclip.sh`
5. In Paperclip, bind the connection's catalogue to the CEO via a tool access
   profile, and add a tool policy requiring approval for `submit_document`,
   `cancel_document`, `delete_document` and `call_method`.

## Why the page is called `ai-chat` and not `ai`

`router.js::convert_to_standard_route` resolves a single-segment desk route
against `frappe.workspaces` **before** it falls through to the Page view. A Page
named `ai` therefore cannot be reached while a public Workspace named `AI`
exists -- `/desk/ai` renders the workspace, always. The workspace ships no
content blocks, so what you get is a page with the right title and an empty
body, and nothing in the console to say why.

The v17 rail entry resolves to the **first `Link` item in the workspace's
authored `sidebar_items`** (`frappe/boot.py::get_sidebar_items`). A workspace
with none authored gets a sidebar generated from its module, whose first link is
the workspace itself -- so the icon lands right back on the blank workspace.
Both halves matter: the page is `ai-chat`, and `sidebar_items[0]` points at it.

`erpnext/patches/v16_0/remove_legacy_ai_page.py` deletes the shadowed `ai` Page
on sites that already have it; Frappe's own orphan sweep did not.

## Two chat channels

The desk tab talks to two different things, switched by the buttons at the top:

| | Board room | CEO |
|---|---|---|
| Endpoint | `POST {paperclip}/api/board/chat/stream` (SSE) | comment on the standing issue |
| Answers | in-request, one turn, ≤120s | asynchronously, as a heartbeat run |
| Can use the ERPNext tools | no | yes, through the gateway |
| Thread | Paperclip's own `Board Operations` issue -- the same transcript the Conference Room UI shows | `ERPNext Operations` |

Board chat is the conversational default; the CEO channel is the one that does
work and raises approvals. Both sides of a board exchange are persisted by
Paperclip as comments (the concierge's own turns carry `authorUserId:
"board-concierge"`), so a dropped request loses the response body but not the
conversation -- the next poll shows it.

Server-side prerequisites live outside this repo: the instance needs
`enableConferenceRoomChat`, and -- because upstream restricts board chat to
`deploymentMode: local_trusted` -- the instance-admin gate patch plus
`PAPERCLIP_BOARD_CHAT_ALLOW_INSTANCE_ADMIN=1`. A `403 DEPLOYMENT_MODE_UNSUPPORTED`
here means an npm update reverted that patch on the Paperclip host. The token in
`PAPERCLIP_BOARD_API_KEY` must belong to the **instance admin** (a company admin
gets 403), and board API keys expire 30 days after mint.

Blast radius: the endpoint spawns an unrestricted `claude` process on the
Paperclip host. `allowed_roles` is the only thing between a desk user and that.
`GUNICORN_TIMEOUT`/`PROXY_READ_TIMEOUT` are set to 180 so the proxy outlives
Paperclip's own 120s cap rather than cutting the answer off at exactly the wrong
moment.

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

## Deploying to production

The image is built from this repo over a git daemon and tagged `main-<sha>`; the
full procedure (bare clone, bridge-only daemon, `--build-arg CACHE_BUST=...`,
bump `CUSTOM_TAG`) is in `.superpowers/sdd/prod-deploy-report.md`. Two things
about *this* module specifically:

- **`--build-arg CACHE_BUST=<something-new>` is mandatory.** The layer that runs
  `bench init` mounts `apps.json` as a build secret, and secret content is not
  part of the cache key -- without a changed CACHE_BUST the build exits 0 having
  rebuilt nothing, and you deploy the previous commit believing otherwise.
- **Run `bench migrate` twice** (or `bench clear-cache` first). Observed on the
  2026-08-10 deploy: the first migrate after an image upgrade that introduces a
  new module created the `AI` Module Def but skipped every doctype under it --
  `AI Settings` did not exist, and the only symptom was
  `No module named 'frappe.core.doctype.ai_settings'` when something touched it,
  which reads like a missing app rather than an unsynced doctype. The second
  migrate synced all three doctypes with no other change.

## Deferred to later specs

Tracked in the roadmap, deliberately not built here: the pgvector RAG index
(spec 2), the five proactive routines and briefing view (spec 3), multi-user
signed identity passthrough, and enabling the CEO's heartbeat (needed only for
routines, not chat).
