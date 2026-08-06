# ERPNext AI — MCP tool server + desk AI tab — design

**Date:** 2026-08-06
**Status:** Approved design, pending implementation plan
**Repo:** erpnext fork, branch `develop` (v17 dev line, `17.0.0-dev`)
**Companion system:** Paperclip (self-hosted AI agent control plane, `https://paperclip.sudiptadhara.in`)
**Scope:** Spec 1 of 3. See `2026-08-06-erpnext-ai-roadmap.md` for specs 2–3 and deferred items.

## 1. Goal

Add an **AI** entry to the ERPNext desk icon rail, between Home and Invoicing, opening a chat agent that knows ERPNext in depth and can perform ERPNext operations — with human approval required for anything that changes the ledger.

**No AI is built here.** The agent is a Paperclip **CEO agent** — Paperclip models a company literally, and the first agent hired into a company is always the CEO (the `role` field is locked on first hire). Paperclip already provides agent hosting, org hierarchy, skills, scheduled routines, goals, budgets, cost tracking, audit, human-in-the-loop approval of deferred tool calls, **and a streaming board chat endpoint**.

This project therefore delivers exactly two things:

1. **ERPNext as a governed MCP tool provider**, so the CEO agent can see and act on the business. This is the substantial work.
2. **A desk chat surface** that talks to Paperclip's board chat and renders approvals inline.

Long-term intent (user's words): the agent becomes a "cofounder / director / co-CEO / co-CTO". That framing maps onto Paperclip's own domain model rather than being bolted on — it ships `role: ceo|cto|cmo|cfo|…`, `reportsTo`, and approval kinds including `approve_ceo_strategy`, `hire_agent` and `request_board_approval`. The autonomy level chosen for this work is **proactive advisor, human decides** — the agent may form its own agenda and draft actions, but every ledger-affecting write waits for a human click. Delegated spend authority is explicitly *not* in scope.

### Decisions made with the user

| Decision | Choice |
|---|---|
| Autonomy | Proactive advisor. Agent proposes; human approves. No autonomous writes |
| Architecture | **A — Paperclip is the brain.** ERPNext provides MCP tools + a thin chat client |
| The agent | The **existing GrihaTEK CEO agent** (`d9654bd8-…`), already provisioned. Not built here |
| Chat transport | `POST /api/board/chat/stream` (board / "conference room" chat), **not** the issue-comment thread. **Feature flag currently OFF** — see §2.1 |
| Model | **Settled by the live agent, not by us**: `adapterType: claude_local`, `model: claude-opus-5`, cheap heartbeat profile `claude-haiku-4-5`. The earlier Kimi/Ollama decision is **moot** for this agent — changing it would mean changing the adapter |
| Tool path | **Hard requirement:** ERPNext MCP is registered as a Paperclip tool connection behind the gateway — **never** in the agent's local Claude Code MCP config. See §6.1 |
| Data egress | Business data goes to Anthropic via Claude Code. Accepted |
| HITL trigger | **Tiered by blast radius.** Reads and draft creation are free; submit / cancel / delete / updates to submitted docs / bulk / over-threshold require approval |
| Permission model (v1) | **Role-gate the AI workspace.** Single service identity mirroring the permissions of the gated role. Multi-user identity passthrough deferred |
| Knowledge (v1) | Live metadata introspection + workspace/URL location map |
| Knowledge (spec 2) | Approved: pgvector RAG index over ERPNext docs. Deferred to its own cycle, **not dropped** |
| Proactive beats | Approved: cash & receivables, pipeline & margin, stock & supply, projects & delivery, company planning. Deferred to spec 3 |
| MCP server placement | Inside Frappe as a whitelisted JSON-RPC endpoint, not a sidecar container |

## 2. Verified facts

Everything below was confirmed against live systems or source on 2026-08-06, not assumed.

### 2.1 Paperclip (live OpenAPI, 475 paths, copy in session scratchpad)

- Instance is up and private: `GET /api/health` → `{"status":"ok","deploymentMode":"authenticated","deploymentExposure":"private","bootstrapStatus":"ready"}`.
- **Remote MCP is a first-class transport.** `POST /api/companies/{companyId}/tools/connections` accepts `transport: "mcp_remote" | "rest_api" | "local_stdio"` and `authKind: "oauth" | "api_key" | "none"`, with `credentialSecretRefs` for stored secrets. No bridge process required.
- **HITL exists at the tool-call layer.** Governed calls defer into action requests: `GET /api/companies/{companyId}/tools/action-requests`, `POST /api/tool-gateway/action-requests/{id}/approve`, `.../decline`. `POST /api/companies/{companyId}/tools/action-requests/{id}/trust-rule` converts an approval into a standing "always allow" rule.
- **Policy is declarative and testable**: `tools/policies` (create, reorder, duplicate), `tools/policy/test` ("Test tool policy decision"), `tools/profiles` bindable to company, agent, project, routine or issue.
- **Tool-count is a solved problem**: named gateways accept `onDemandToolsConfig: {enabled, searchToolName: "search_tools", runToolName: "run_tool"}`, so a wide tool surface does not have to be loaded into context up-front.
- **Traceability**: `metadataPolicy` can forward `companyId`, `gatewayId`, `projectId`, `issueId`, `agentId`, `runId`, `correlationId` to the tool server. Every ERPNext mutation can be tied to the exact agent run that caused it.
- **A CEO agent is a first-class concept.** `POST /api/companies/{companyId}/agents` takes `role` from `["ceo","cto","cmo","cfo","security","engineer","designer","pm","qa","devops","researcher","general"]`, plus `reportsTo` (org hierarchy), `capabilities`, `desiredSkills`, `adapterType`, `budgetMonthlyCents` and `permissions`. Per Paperclip's documentation the **first agent hired into a company is locked to `ceo`**. Approval kinds include `approve_ceo_strategy`, `hire_agent`, `budget_override_required`, `request_board_approval`.
- **Streaming board chat exists but is currently DISABLED.** `POST /api/board/chat/stream`, body `{companyId, message, taskId}`. Probed live on 2026-08-06 with an agent key: returns `403 {"error":"Conference Room Chat is not enabled","code":"FEATURE_DISABLED"}`. The endpoint is real and the request shape is accepted; **`enableConferenceRoomChat` must be switched on** at `PATCH /api/instance/settings/experimental` (board access) before the desk chat can work. Until then the stream format (SSE vs chunked JSON — it declares `application/json`) also remains unverified, so the SPA transport must abstract both (§7.1).
- **Paperclip runs no LLM of its own.** Agents are external runtimes selected by `adapterType`: `["process","http","claude_local","codex_local","cursor_cloud","gemini_local","grok_local","hermes_gateway","hermes_local","opencode_local","pi_local","cursor","openclaw_gateway"]`. A Paperclip agent is effectively a coding-agent CLI running in an execution workspace — which is why MCP is the natural tool interface. **Consequence:** model choice is constrained by adapter (see the decisions table).
- **Agents are not continuous.** They wake in *heartbeats*, work, and sleep, holding no context and consuming no budget in between — matching the `heartbeat-runs` endpoints.
- Work threads also exist (`POST /api/issues/{id}/comments`, `GET /api/issues/{issueId}/active-run`, `/live-runs`, `/runs`) and remain the surface for routine output and long-running tasks, but they are **not** the chat path.
- Feature flags observed on instance settings, relevant here: `enableConferenceRoomChat`, `enableBuiltInAgents`, `enableDecisions`, `enableGoalsSidebarLink`, `enableSummaries`, `enableTaskWatchdogs`.
- Also present and reused rather than rebuilt: `routines` (+ triggers, revisions, runs), `heartbeat-runs` (+ events, log, watchdog decisions), `goals`, `costs`, `budgets` (per agent and per company), `secrets`, `skills` (versioned, forkable, test-runnable), `tool-gateway/audit`.
- Auth schemes: `BoardSessionAuth` (cookie, Better Auth), `BoardApiKeyAuth` (bearer), `AgentBearerAuth` (bearer).

### 2.2 ERPNext / Frappe

- Frappe and ERPNext both report `17.0.0-dev` in the running production container.
- The desk renders public workspaces as a **vertical icon rail**, ordered by `sequence_id`. Verified live: Home `1.0`, Invoicing `2.0`. **`sequence_id: 1.5` places AI exactly between them.**
- The `Workspace` doctype supports `type: "Workspace" | "Link" | "URL"` with `link_type: "DocType" | "Page" | "Report"` and `external_link`. Six frappe-core workspaces already use link types, so a nav entry that opens a custom page needs **no core patch and no JS injection**.
- Desk routes are served under `/desk/...` in this version (`/app/home` redirects to `/desk/home`).
- Surface area: **556 doctype directories**, 83 submittable doctypes, 188 reports, 808 `@frappe.whitelist` methods across 21 modules. Confirms generic meta-tools over hand-written per-doctype tools.
- Fork conventions that constrain this work (from `hooks.py` and prior cycles): `require_type_annotated_api_methods = True` (every whitelisted method must be fully type-annotated), `use_json_request_body` (native JSON bodies — matches JSON-RPC), and a `postgres-compat` pre-commit hook banning raw SQL in favour of `frappe.qb`.
- SPA precedent exists in-repo: `banking/` is React 19 + Vite 8 + Tailwind 4 + radix + `frappe-react-sdk`, wired via `[tool.bench.assets]` in `pyproject.toml`, a boot shell at `erpnext/www/banking.py`, and a catch-all rule at `hooks.py:220`. It already depends on `react-markdown`, `remark-gfm`, `sonner`, `cmdk` and `jotai`. **Constraint:** `[tool.bench.assets]` is a single table, so a second SPA needs its own build script rather than bench's default wiring (§5.2).

### 2.3 Model and vector infrastructure

- LiteLLM gateway fronts Ollama at `nuc.lan:11434`.
**Superseded for the CEO agent** — it runs `claude_local` / `claude-opus-5` (§2.3a), so nothing below is on the critical path. Retained because it stays relevant to spec 2's embedding/rerank work and to any future agent that does route through the local gateway:

- `kimi-k2.6:cloud` **verified working with tool calling** — a live probe returned a well-formed `tool_calls` block with reasoning.
- `kimi-k3:cloud` exists on the registry but the account returns *"this model uses extra usage only … your extra usage balance is empty"*. K3 has no local weights (no llama.cpp/GGUF path), so "K3 via Ollama" means **Ollama Cloud**.
- Genuinely local alternatives if egress ever becomes unacceptable: `gpt-oss:20b`, `qwen3:14b`, `mistral-small:24b`.
- `nomic-embed-text` returns **768 dimensions**; reranker `mixedbread-ai/mxbai-rerank-large-v1` is live at `nuc.lan:7997`. (Used in spec 2.)
- The site's PostgreSQL 18.4 has **pgvector 0.8.2 available** (not yet installed), and `erpnext_db_user` is a superuser, so `CREATE EXTENSION vector` will succeed. (Used in spec 2.)

### 2.3a The live GrihaTEK company (probed 2026-08-06 with an agent key)

| Fact | Value |
|---|---|
| Paperclip company id | `06bb12c4-648e-4e05-a5ef-e32a8ee3ec06` (slug `GRI`) |
| CEO agent id | `d9654bd8-6382-4c3b-9b9b-050d533a59a8`, `urlKey: ceo`, status `idle`, created 2026-08-06 |
| CEO runtime | `adapterType: claude_local`, `model: claude-opus-5`; cheap heartbeat profile `claude-haiku-4-5` |
| Other agents | **CTO** (`claude_local`, opus-5), Reflection Coach (paused), Summarizer (paused) |
| Budget | `budgetMonthlyCents: 0`, `spentMonthlyCents: 0` — **no cap set** |
| Existing skills | `grihatek-company-facts`, `grihatek-brand`, `grihatek-quote-builder`, `grihatek-grant-watch`, `grihatek-pcb-review`, plus paperclip-* and memory skills |
| Business | GRIHATEK IT SOLUTIONS PRIVATE LIMITED, CIN U62091WR2026PTC295515, inc. 24 July 2026, West Bengal. Smart-home IoT: sensing hardware, ML layer, integration services. Flagship **RoomGuardian V2** |
| Host topology | Paperclip runs on a Mac (`/Users/dharabot/.paperclip/…`) on the same LAN as ERPNext. `nuc.lan` = `192.168.10.118`; ERPNext frontend `:4410` responds `200`. `mcp_remote` must use the **LAN** address, not the public hostname |

**Highest-value integration point already present:** the `grihatek-quote-builder` skill *"builds costed customer quotes for GrihaTEK home and building automation integration projects."* Today that produces files. With the ERPNext tool connection it can produce real `Quotation` documents against real `Item`, `Price List` and `Customer` records — with submission gated by approval.

### 2.4 Landscape

ERPNext core ships no native AI. Five third-party apps exist (NextAI, ChatNext, ChangAI, Aerele Chatbot, Composio MCP bridge) and **none implement mandatory human approval for write operations**. The HITL requirement is the differentiator of this design, not a formality.

## 3. Architecture

```
┌─ ERPNext desk ──────────────────────┐        ┌─ Paperclip ───────────────┐
│  icon rail: Home · AI · Invoicing…  │        │  CEO agent (first hire)    │
│  Workspace(type=Link → Page "ai")   │        │   adapterType → CLI runtime│
│         │                            │        │   ├ Skills · Goals        │
│  React SPA (chat)                   │        │   ├ Routines (heartbeats) │
│   · POST /api/board/chat/stream ────┼───────▶│   ├ Tool policies         │
│   · approve/decline inline          │        │   ├ Budget + Costs        │
│   · run/approval state              │        │   └ Approvals / Audit     │
└─────────────────────────────────────┘        └────────────┬──────────────┘
              ▲                                              │ mcp_remote
              │                                              ▼
        ┌─────┴──────────────────────────────────────────────────────┐
        │  erpnext.ai.mcp — whitelisted JSON-RPC endpoint            │
        │  runs as the ERPNext user bound to the API key             │
        │  Frappe enforces every permission natively                 │
        └────────────────────────────────────────────────────────────┘
```

**Why the MCP server lives inside Frappe.** MCP's streamable-HTTP transport is JSON-RPC over `POST`; SSE is only needed for server-initiated messages, which a pure tool server never sends. So the whole transport is one type-annotated `@frappe.whitelist(methods=["POST"], allow_guest=False)` method. This avoids a seventh container and a second auth hop, and — the decisive reason — `frappe.session.user` is already correct inside the request, so document permissions, user permissions, `if_owner` rules and field-level permissions are enforced by Frappe itself instead of being reimplemented. A sidecar would have to re-derive all of that or proxy back into Frappe anyway.

**Ports.** The endpoint is a path on the existing frontend (`4410`), so the 440–4499 port policy needs no new allocation.

### 3.1 Components

| Path | Owns | Depends on |
|---|---|---|
| `erpnext/ai/mcp.py` | JSON-RPC dispatch: `initialize`, `tools/list`, `tools/call`; error mapping | Frappe ORM |
| `erpnext/ai/tools/` | One module per tool group: `discovery.py`, `documents.py`, `reports.py`, `methods.py` | `mcp.py` contract |
| `erpnext/ai/registry.py` | Tool registration, JSON Schema generation, enablement/cap checks | AI Settings |
| `erpnext/ai/knowledge.py` | Metadata introspection, workspace/module/URL location map | Frappe meta |
| `erpnext/ai/doctype/ai_settings/` | Single: Paperclip URL, board API key, company/agent id, enabled tools, caps | — |
| `erpnext/ai/paperclip.py` | Typed server-side client for the SPA's Paperclip calls | AI Settings |
| `erpnext/ai/workspace/ai/ai.json` | Nav entry, `sequence_id: 1.5`, `type: Link → Page` | — |
| `erpnext/ai/page/ai/` | Desk Page that mounts the SPA bundle | `ai/` build output |
| `ai/` (repo root) | React SPA: chat thread, approval cards, tool-call transcript | Paperclip REST |

New module `AI` appended to `erpnext/modules.txt`.

### 3.2 AI Settings (new Single, module AI)

Fields: `enabled` (Check), `paperclip_url` (Data), `paperclip_company_id` (Data), `board_api_key` (Password), `agent_id` (Data, the CEO agent), `erpnext_company` (Link → Company, **required when enabled**), `additional_companies` (Table MultiSelect → Company, default empty), `allowed_roles` (Table MultiSelect → Role), `enabled_tools` (Small Text, JSON list), `max_batch_size` (Int, default 20), `max_document_value` (Currency, default 0 = unlimited), `allowed_methods` (Code/JSON, allowlist for `call_method`).

**`paperclip_company_id` and `erpnext_company` are different things and must never be conflated.** The first is Paperclip's org/tenant that owns the agent; the second is the ERPNext accounting entity whose books the agent operates on. They are named apart deliberately — an earlier draft of this spec called both "company" and silently omitted the mapping between them. See §3.3.

Deliberately **absent**: model and adapter settings. Those belong to the Paperclip agent (`adapterType`, `adapterConfig`, `budgetMonthlyCents`) and duplicating them in ERPNext would create two sources of truth that silently drift.

### 3.3 Company scoping — how the agent knows *whose* books it is reading

`company` is a mandatory filter on nearly every transactional doctype in ERPNext. An agent that does not know which company it operates in produces queries that are ambiguous at best, and in a multi-entity install **mixes the books of separate legal entities**. This is a correctness and compliance property, not a convenience.

Rules:

1. **One bound company by default.** `erpnext_company` is the agent's operating entity. The live install currently has exactly one — `GrihaTek It Solutions Pvt Ltd` (GISPL, INR, India, FY 2026-2027) — but the binding is explicit rather than inferred from `Global Defaults`, so adding a second company later cannot silently widen the agent's reach.
2. **Company-scoped tools inject the filter server-side.** For any doctype carrying a `company` field, `search_documents`, `run_report` and `create_document` default to the bound company. A request naming a company outside `erpnext_company + additional_companies` is **refused**, not silently re-scoped — an agent asking for the wrong entity is a bug worth surfacing.
3. **Cross-company work is opt-in.** `additional_companies` is empty by default. Populating it is a deliberate act.

### 3.4 Orientation — how the agent knows what ERPNext *is*

Three layers, in increasing persistence:

**MCP `initialize` instructions (per connection, generated live).** MCP's `InitializeResult` carries an `instructions` string. Ours is built from the running install, never hardcoded: ERPNext version, bound company with its currency/country/fiscal year, active modules, the draft→submit lifecycle, which tools require approval, and the company-scoping rules above. Because it is generated, it cannot drift from reality.

**A dedicated `get_company_context()` tool (on demand).** Returns the bound company plus currency, country, fiscal year, chart-of-accounts roots, active modules, and headline counts (customers, suppliers, items, open orders). This is the agent's first call when it needs to ground itself, and it is cheap enough to call at the start of any routine.

**`instructionsBundle` and Skills (durable).** Paperclip agents accept `instructionsBundle: {entryFile, files}` and `desiredSkills`. Business doctrine lives here — how this company wants its books handled, which reports matter, what counts as overdue — versioned and test-runnable rather than buried in a prompt. Authored during Paperclip setup, not by ERPNext code.

The division is deliberate: **ERPNext supplies facts, Paperclip supplies judgment.** Facts are generated so they cannot go stale; judgment is versioned so changes are reviewable.

Secrets live here as Password fields read via `get_password()`, never in env files — matching the TaskPilot Settings precedent. A "Test Connection" button calls Paperclip `GET /api/health`.

## 4. Tool surface

Fourteen tools. ERPNext's surface is uniform — every doctype is CRUD plus optional submit/cancel — so tools are generic and driven by live metadata rather than hand-written per doctype.

| Tool | Tier | Behaviour |
|---|---|---|
| `get_company_context()` | free | Bound company, currency, country, fiscal year, chart-of-accounts roots, active modules, headline counts. The grounding call (§3.4) |
| `search_doctypes(query, module?)` | free | Fuzzy over label/module/description. Returns module, owning workspace, and desk URL |
| `describe_doctype(doctype)` | free | Fields, types, options, links, mandatory, naming, workflow states, `is_submittable`, **and the caller's effective permissions** |
| `list_reports(module?)` | free | Report discovery |
| `search_documents(doctype, filters?, fields?, order_by?, limit?, start?)` | free | Frappe filter syntax, paginated, `limit` capped |
| `get_document(doctype, name)` | free | Full document including child tables |
| `run_report(report, filters?)` | free | Query and script reports |
| `get_workspace_map()` | free | Doctype → workspace → module → desk URL, for "where is what" |
| `create_document(doctype, data, idempotency_key)` | free | Always lands as **draft** (`docstatus 0`) |
| `update_document(doctype, name, data)` | free if draft | **approval** when `docstatus != 0` |
| `submit_document(doctype, name)` | **approval** | Posts to GL / stock ledger |
| `cancel_document(doctype, name)` | **approval** | |
| `delete_document(doctype, name)` | **approval** | |
| `call_method(method, args)` | **approval** | Restricted to the `allowed_methods` allowlist |

### 4.1 Where the tier is enforced, and why in two places

**Primary enforcement is Paperclip `tools/policies`.** It is declarative, testable via `tools/policy/test`, produces the deferred action request the chat approves, and supports promotion to a standing trust rule. ERPNext does not duplicate this and does not implement an approval queue.

**Secondary enforcement is a bounded kill switch in ERPNext.** The MCP endpoint is reachable by anything holding the API key, so the server independently enforces: the `enabled_tools` allowlist, `max_batch_size`, `max_document_value`, and the `allowed_methods` allowlist for `call_method`. If a Paperclip policy is misconfigured, or a different agent is pointed at the endpoint, blast radius stays bounded. Two layers at a trust boundary is defence in depth, not duplication.

### 4.2 Idempotency — mandatory, not optional

Agent runs retry. A retried `create_document` whose first attempt actually succeeded will create a second purchase order or invoice. Therefore `create_document` requires an `idempotency_key`, persisted on the created document; a repeat key returns the original document instead of creating a new one. Without this, the first network blip during an unattended run silently invents documents.

## 5. Desk surface

### 5.1 Nav entry

A standard `Workspace` fixture in the new AI module: `label: "AI"`, `public: 1`, `sequence_id: 1.5`, `icon: "sparkles"`, `type: "Link"`, `link_type: "Page"`, `link_to: "ai"`, and `roles` populated from the gated role. Because `sequence_id` orders the icon rail, this lands between Home (`1.0`) and Invoicing (`2.0`) with no core modification.

### 5.2 Chat SPA

A new `ai/` SPA at repo root reusing the `banking/` stack: React 19 + Vite + Tailwind + `frappe-react-sdk`.

**Delivery mechanism — decided, because the two options are not interchangeable.** The SPA mounts into a **Frappe desk Page** named `ai` (route `/desk/ai`), which is what `link_type: "Page"` on the workspace resolves to. The desk shell — and therefore the icon rail the user asked to appear in — stays visible while chatting. Vite builds in library/IIFE mode to `erpnext/public/ai/`, exposing a single `mountAI(element)` entry that the page's `page.js` calls. `frappe.boot`, the CSRF token and the session user are already present in the desk, so no separate boot shell is needed.

This deliberately **diverges from the `banking/` precedent**, which is a standalone website route (`/banking/<path>` via `website_route_rules`) that navigates away from the desk and loses the rail. That trade is right for a full-page tool and wrong for a chat tab. If IIFE mounting inside a desk Page proves troublesome, the documented fallback is the banking pattern with `type: "URL"` on the workspace.

Because `[tool.bench.assets]` holds only one build config (already claimed by `banking`), the AI SPA gets its own `yarn build:ai` script rather than bench's default wiring — called out in the plan so it is not mistaken for a misconfiguration.

Screens: a **streaming chat thread** driven by `POST /api/board/chat/stream` with `{companyId, message, taskId}`; a tool-call transcript showing what the agent did; **inline approval cards** for pending action requests calling `POST /api/tool-gateway/action-requests/{id}/approve|decline`, so approvals never require leaving ERPNext; and a settings-gated empty state when AI Settings is not configured.

Because agents run in heartbeats rather than continuously, the UI must render agent state honestly — idle, waking, running, awaiting approval — rather than implying a always-on presence.

All Paperclip calls are proxied through thin whitelisted ERPNext methods (`erpnext/ai/paperclip.py`) rather than issued from the browser, so the board API key is never exposed to the client and every proxy method re-checks the caller's role.

## 6. Security model

### 6.1 The gateway is load-bearing — ERPNext MCP must NEVER be wired directly into the agent

The live CEO agent runs with **`dangerouslySkipPermissions: true`** in its `adapterConfig`. Claude Code therefore auto-approves its own tool calls with no prompt. The consequence is absolute:

> If the ERPNext MCP server were added to the agent's local Claude Code MCP config, **every HITL control in this design would be silently bypassed.** The agent would submit invoices, cancel documents and delete records with no human in the loop and no audit trail, and nothing would appear to be wrong.

Human approval exists **only** because tool calls traverse Paperclip's tool gateway, which suspends governed calls into action requests. So:

- ERPNext is registered as a Paperclip **tool connection** (`transport: mcp_remote`) and bound to the agent through a **tool access profile**.
- The agent reaches ERPNext **exclusively** through the gateway's MCP endpoint.
- Adding `erpnext` to any agent's local MCP config is a **defect**, not a shortcut. This belongs in the implementation plan as an explicit check.

The ERPNext-side caps in §4.1 exist precisely because this invariant can be broken by configuration elsewhere, outside this repo's control.

Two related observations from the live probe, worth acting on but outside this spec: the CEO has `budgetMonthlyCents: 0` (no spend cap), and `dangerouslySkipPermissions` is broad — it governs the agent's local filesystem and shell access too, not just ERPNext.

### 6.2 Single service identity

**The cost of architecture A, stated plainly.** Paperclip authenticates to ERPNext with a single API key, so the agent is one ERPNext identity regardless of who is chatting. Permission checks run against that service account, not the asker. A low-privilege user chatting with a privileged agent could otherwise read data they cannot see in the desk.

v1 mitigation, chosen deliberately: **role-gate the AI workspace.** The workspace and every Paperclip proxy method are restricted to roles listed in AI Settings (initially the user alone). The service account is provisioned with exactly the permissions those roles already hold. This matches the "cofounder" framing — it is not a feature for every desk user.

Additional controls: the MCP endpoint rejects requests when `enabled` is off; rate limiting per API key; audit via Paperclip's `tool-gateway/audit` correlated with `runId`/`correlationId` forwarded by `metadataPolicy`; and `PermissionError` responses that say "not permitted" without revealing whether a hidden record exists.

**Deferred (spec 2 backlog, explicitly not forgotten):** signed identity passthrough so any ERPNext user can use the AI tab under their own permissions.

## 7. Error handling

### 7.1 Latency and the conference-room flag

`POST /api/board/chat/stream` streams the reply, so the original latency objection to architecture A no longer applies and **architecture B is not needed**. Two residual risks remain, both resolvable only with a board API key:

- **The flag may be off.** `enableConferenceRoomChat` is an experimental instance setting. If it cannot be enabled, the fallback is the `Issue → comments` work thread with polled `active-run` / `live-runs` state — noticeably slower, and the point at which architecture B would be worth reconsidering.
- **The stream format is undeclared.** The endpoint advertises `application/json`, not `text/event-stream`. The SPA's transport layer must be written so SSE and chunked JSON are interchangeable behind one interface, rather than assuming either.

Separately, heartbeat scheduling means a first message may wait for the agent to wake. That is a property of the platform, not a defect, and the UI surfaces it rather than hiding it.

### 7.2 Failure matrix

| Failure | Behaviour |
|---|---|
| Paperclip unreachable | AI tab shows a degraded banner. Rest of the desk unaffected — it is a separate workspace |
| AI Settings unconfigured | Nav item present, tab shows a configuration prompt. No errors raised |
| `enableConferenceRoomChat` off | Tab explains the flag is required and falls back to the issue-thread path (§7.1) |
| CEO agent asleep | Chat shows "waking" state; message is queued, not lost |
| Model/adapter unavailable | Handled inside Paperclip by the agent's adapter config; ERPNext surfaces the error verbatim |
| `frappe.PermissionError` in a tool | JSON-RPC error with a clear, non-leaky message the agent can reason about |
| Validation error on create/update | Returned verbatim so the agent can correct and retry |
| Cap exceeded | Tool refuses with the cap named, so the agent can split the work |
| Duplicate retry | Idempotency key returns the original document |
| Unknown tool / malformed JSON-RPC | Standard JSON-RPC error codes; never a stack trace |

## 8. Testing

- Unit tests per tool on a test site, using ERPNext's existing test infrastructure.
- **Permission tests through the MCP path** — assert a low-privilege service identity is denied, rather than only testing Frappe's own permission layer.
- HITL tests — assert `submit` / `cancel` / `delete` cannot execute without an approved action request.
- Cap tests — `max_batch_size`, `max_document_value`, `enabled_tools`, `allowed_methods`.
- Idempotency test — the same key twice yields one document.
- Live smoke — connect Paperclip, exercise every tool once, confirm an approval round-trip end to end and that audit records carry the run id.

## 9. Out of scope for this spec

Deferred with intent, tracked in `2026-08-06-erpnext-ai-roadmap.md`: the pgvector RAG knowledge index (approved, spec 2); the five proactive routines and the briefing view (approved, spec 3); multi-user identity passthrough; delegated spend authority; voice I/O (Kokoro TTS and Whisper are available on the stack); and narrowing the ERPNext Postgres role from superuser.

## 10. Required inputs before implementation can be verified end to end

Resolved on 2026-08-06 by probing with the CEO agent key:

- ✅ Paperclip company id — `06bb12c4-648e-4e05-a5ef-e32a8ee3ec06`
- ✅ CEO agent exists — `d9654bd8-6382-4c3b-9b9b-050d533a59a8`
- ✅ Adapter and model — `claude_local` / `claude-opus-5`. Kimi/Ollama question closed
- ✅ Network — Paperclip and ERPNext share a LAN; `nuc.lan` = `192.168.10.118`, `:4410` responds `200`

Still required:

1. **A board API key.** The supplied key is an *agent* key: it reads `agents/me`, skills, goals and routines, but every `tools/*` route returns `403 Board access required`. Creating the tool connection, the gateway, and the tool policies all need board scope. **This is the blocking credential.**
2. **Enable `enableConferenceRoomChat`** via `PATCH /api/instance/settings/experimental`. Confirmed off — board chat returns `FEATURE_DISABLED`. Without it there is no desk chat, only the slower issue-thread fallback (§7.1).
3. **The LAN address ERPNext will be reached at** — `http://192.168.10.118:4410` unless the Paperclip host resolves `nuc.lan`. The public hostname must not be used.
4. The **role** that gates the AI workspace (default: System Manager).
5. Optional but recommended: a **spend cap** on the CEO agent (`budgetMonthlyCents` is currently `0`).

Items 1–3 block the live smoke test and the chat transport decision. The **MCP tool server — the bulk of the work — can be built and unit-tested without any of them.**
