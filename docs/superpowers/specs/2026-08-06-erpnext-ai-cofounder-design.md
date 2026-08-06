# ERPNext AI — MCP tool server + desk AI tab — design

**Date:** 2026-08-06
**Status:** Approved design, pending implementation plan
**Repo:** erpnext fork, branch `develop` (v17 dev line, `17.0.0-dev`)
**Companion system:** Paperclip (self-hosted AI agent control plane, `https://paperclip.sudiptadhara.in`)
**Scope:** Spec 1 of 3. See `2026-08-06-erpnext-ai-roadmap.md` for specs 2–3 and deferred items.

## 1. Goal

Add an **AI** entry to the ERPNext desk icon rail, between Home and Invoicing, opening a chat agent that knows ERPNext in depth and can perform ERPNext operations — with human approval required for anything that changes the ledger.

The agent itself does **not** live in ERPNext. Paperclip already implements agent hosting, skills, scheduled routines, goals, budgets, cost tracking, audit and — critically — human-in-the-loop approval of deferred tool calls. This project makes ERPNext a **governed tool provider** for that platform, plus a chat surface in the desk.

Long-term intent (user's words): the agent becomes a "cofounder / director / co-CEO / co-CTO". The autonomy level chosen for this work is **proactive advisor, human decides** — the agent may form its own agenda and draft actions, but every ledger-affecting write waits for a human click. Delegated spend authority is explicitly *not* in scope.

### Decisions made with the user

| Decision | Choice |
|---|---|
| Autonomy | Proactive advisor. Agent proposes; human approves. No autonomous writes |
| Architecture | **A — Paperclip is the brain.** ERPNext provides MCP tools + a thin chat client |
| Model | Pluggable via the existing LiteLLM gateway. Default `kimi-k3:cloud`, fallback `kimi-k2.6:cloud` |
| Data egress | Cloud model routing accepted for business data |
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
- **Conversation model is `Issue → runs → comments`**: `POST /api/issues/{id}/comments`, `GET /api/issues/{issueId}/active-run`, `GET /api/issues/{issueId}/live-runs`, `GET /api/issues/{id}/runs`. This is a work thread, not a low-latency chat channel — see §7.1.
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
- `kimi-k2.6:cloud` **verified working with tool calling** — a live probe returned a well-formed `tool_calls` block with reasoning. This is the fallback, and it is known-good.
- `kimi-k3:cloud` exists on the registry but the account currently returns *"this model uses extra usage only … your extra usage balance is empty"*. K3 has no local weights (no llama.cpp/GGUF path), so "K3 via Ollama" means **Ollama Cloud**. Requires topping up before use.
- Genuinely local alternatives if egress ever becomes unacceptable: `gpt-oss:20b`, `qwen3:14b`, `mistral-small:24b`.
- `nomic-embed-text` returns **768 dimensions**; reranker `mixedbread-ai/mxbai-rerank-large-v1` is live at `nuc.lan:7997`. (Used in spec 2.)
- The site's PostgreSQL 18.4 has **pgvector 0.8.2 available** (not yet installed), and `erpnext_db_user` is a superuser, so `CREATE EXTENSION vector` will succeed. (Used in spec 2.)

### 2.4 Landscape

ERPNext core ships no native AI. Five third-party apps exist (NextAI, ChatNext, ChangAI, Aerele Chatbot, Composio MCP bridge) and **none implement mandatory human approval for write operations**. The HITL requirement is the differentiator of this design, not a formality.

## 3. Architecture

```
┌─ ERPNext desk ──────────────────────┐        ┌─ Paperclip ───────────────┐
│  icon rail: Home · AI · Invoicing…  │        │  Agent "Co-founder"        │
│  Workspace(type=Link → Page "ai")   │        │   ├ Skills                 │
│         │                            │        │   ├ Tool policies         │
│  React SPA (chat)  ───REST───────────┼───────▶│   ├ Budget + Costs        │
│   · issue thread + comments         │        │   └ Approvals / Audit     │
│   · approve/decline inline          │        └────────────┬──────────────┘
└─────────────────────────────────────┘                     │ mcp_remote
              ▲                                              ▼
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

Fields: `enabled` (Check), `paperclip_url` (Data), `board_api_key` (Password), `company_id` (Data), `agent_id` (Data), `default_model` (Data, default `kimi-k3:cloud`), `fallback_model` (Data, default `kimi-k2.6:cloud`), `allowed_roles` (Table MultiSelect → Role), `enabled_tools` (Small Text, JSON list), `max_batch_size` (Int, default 20), `max_document_value` (Currency, default 0 = unlimited), `allowed_methods` (Code/JSON, allowlist for `call_method`).

Secrets live here as Password fields read via `get_password()`, never in env files — matching the TaskPilot Settings precedent. A "Test Connection" button calls Paperclip `GET /api/health`.

## 4. Tool surface

Thirteen tools. ERPNext's surface is uniform — every doctype is CRUD plus optional submit/cancel — so tools are generic and driven by live metadata rather than hand-written per doctype.

| Tool | Tier | Behaviour |
|---|---|---|
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

Screens: a chat thread mapped onto a Paperclip issue; a tool-call transcript showing what the agent did; **inline approval cards** for pending action requests calling `POST /api/tool-gateway/action-requests/{id}/approve|decline`, so approvals never require leaving ERPNext; and a settings-gated empty state when AI Settings is not configured.

All Paperclip calls are proxied through thin whitelisted ERPNext methods (`erpnext/ai/paperclip.py`) rather than issued from the browser, so the board API key is never exposed to the client and every proxy method re-checks the caller's role.

## 6. Security model

**The cost of architecture A, stated plainly.** Paperclip authenticates to ERPNext with a single API key, so the agent is one ERPNext identity regardless of who is chatting. Permission checks run against that service account, not the asker. A low-privilege user chatting with a privileged agent could otherwise read data they cannot see in the desk.

v1 mitigation, chosen deliberately: **role-gate the AI workspace.** The workspace and every Paperclip proxy method are restricted to roles listed in AI Settings (initially the user alone). The service account is provisioned with exactly the permissions those roles already hold. This matches the "cofounder" framing — it is not a feature for every desk user.

Additional controls: the MCP endpoint rejects requests when `enabled` is off; rate limiting per API key; audit via Paperclip's `tool-gateway/audit` correlated with `runId`/`correlationId` forwarded by `metadataPolicy`; and `PermissionError` responses that say "not permitted" without revealing whether a hidden record exists.

**Deferred (spec 2 backlog, explicitly not forgotten):** signed identity passthrough so any ERPNext user can use the AI tab under their own permissions.

## 7. Error handling

### 7.1 Latency

Paperclip's conversation model is `Issue → runs → comments`, built for work rather than chat, so replies arrive by polling `active-run` / `live-runs` rather than streaming tokens. The SPA must therefore show run state honestly (queued / running / awaiting approval / done) instead of pretending to be an instant chat. If this proves too slow in use, the escape hatch is architecture B — a local chat loop that still routes every tool call through Paperclip's gateway — recorded in the roadmap.

### 7.2 Failure matrix

| Failure | Behaviour |
|---|---|
| Paperclip unreachable | AI tab shows a degraded banner. Rest of the desk unaffected — it is a separate workspace |
| AI Settings unconfigured | Nav item present, tab shows a configuration prompt. No errors raised |
| K3 unbilled / unavailable | Paperclip agent falls back to `kimi-k2.6:cloud` |
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

1. Paperclip **board API key** and the target **company id**.
2. A Paperclip **agent** provisioned for this work (or approval to create one).
3. **Ollama credit** for `kimi-k3:cloud`, or explicit acceptance of running on `kimi-k2.6:cloud`.
4. Confirmation that the Paperclip host can **reach the ERPNext frontend** (`nuc.lan:4410`) over the network — `mcp_remote` requires Paperclip to dial ERPNext, not the reverse.
5. The **role** that gates the AI workspace (default: System Manager).

Items 1–4 block the live smoke test only. All code and unit tests can be built and verified without them.
