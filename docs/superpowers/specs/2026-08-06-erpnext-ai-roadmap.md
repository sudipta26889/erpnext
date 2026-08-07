# ERPNext AI — roadmap and deferred items

**Date:** 2026-08-06
**Status:** Living backlog. Companion to `2026-08-06-erpnext-ai-cofounder-design.md` (spec 1).

Everything here was discussed and **approved in principle** during brainstorming, then deliberately deferred so spec 1 could ship something visible and working. Nothing in this file was rejected. Its purpose is to make sure none of it is quietly lost.

---

## Spec 2 — pgvector knowledge index (approved, deferred)

The user explicitly asked for a comprehensive ERPNext knowledge base and chose to build the RAG index despite the recommendation to start with metadata alone. It is scheduled, not dropped.

**Infrastructure is already verified present** (2026-08-06):

- pgvector **0.8.2 available** on the site's PostgreSQL 18.4; `erpnext_db_user` is superuser, so `CREATE EXTENSION vector` will succeed.
- `nomic-embed-text` via Ollama → **768 dimensions**.
- Reranker `mixedbread-ai/mxbai-rerank-large-v1` live at `nuc.lan:7997`.
- No new container required.

**Design sketch**

- `AI Knowledge Chunk` doctype: `source_url`, `title`, `doctype_ref`, `module`, `heading_path`, `content`, `token_count`, `content_hash`.
- Embeddings in a **side table** `__ai_knowledge_vec(chunk_name PK, embedding vector(768))` with an HNSW index, created by a patch. Deliberately *not* a Frappe-managed column so `bench migrate` never touches it.
- Retrieval: embed query → pgvector cosine top-50 → mxbai rerank → top-8. Exposed as a fifteenth MCP tool, `search_knowledge(query)`, alongside spec 1's fourteen.
- Re-index: weekly scheduler job diffing `content_hash`.

**Three ingestion sources**

1. The official ERPNext manual (procedural: "how do I run a period close").
2. Auto-generated cards per doctype from live metadata, so *this install's* customizations are searchable as prose.
3. A notes doctype for institutional knowledge — the "how we do it here" that no public document contains.

**Known conflict to resolve in the plan.** This repo's `postgres-compat` pre-commit hook bans raw SQL in favour of `frappe.qb`, and a pgvector similarity search cannot be expressed in the query builder. This needs a **narrow, documented carve-out for the retrieval function only** — not a blanket exemption.

**Open risk.** Chunks from public docs go stale as ERPNext versions move, while live metadata never does. If retrieval quality degrades, prefer widening source 2 over re-scraping source 1.

---

## Spec 3 — proactive routines and the briefing (approved, deferred)

This is where "cofounder" actually becomes real. Each beat is a **Paperclip Routine** on a schedule that calls ERPNext MCP tools and produces an Issue containing findings plus *drafted* actions, surfaced in the desk as deferred action requests to approve inline.

The user selected **all** of these, plus general company work:

| Beat | Watches | Drafts |
|---|---|---|
| Cash & receivables | Overdue invoices, worsening ageing buckets, cash vs upcoming payables, customers near credit limit | Payment reminders, dunning follow-ups |
| Sales pipeline & margin | Stalled opportunities/quotations, win-rate drift, orders below target margin, price-list vs actual-price gaps | Follow-ups, re-quotes |
| Stock & supply | Items below reorder level, stock-outs blocking open sales orders, dead inventory, overdue purchase receipts | Material requests, draft POs |
| Projects & delivery | Projects past expected end date, overdue tasks, budget burn vs percent complete, unbilled billable time | — (reads through the TaskPilot-backed Projects module) |
| Company planning & research | Goal-driven rather than threshold-driven; weekly | Plans, research memos, proposals |

The planning beat is the one that distinguishes an advisor from a monitor — it should be driven by Paperclip **Goals**, not by thresholds.

**Deliverable:** a "Morning Briefing" view in the AI tab aggregating the latest routine issues.

---

## Deferred: multi-user identity passthrough

**Why it matters.** Architecture A authenticates Paperclip to ERPNext with a single API key, so the agent is one ERPNext identity no matter who is chatting. Permission checks run against the service account, not the asker. v1 works around this by role-gating the AI workspace to a single trusted role.

The moment a second, less-privileged person should use the AI tab, this must be solved. Options, in preference order:

1. **Signed identity passthrough.** The SPA sends the ERPNext user with each issue; `metadataPolicy` forwards `correlationId`; the MCP server re-executes tool calls as that user. Must use a **signed** token — an unsigned forwarded identity claim is security theatre.
2. **Per-user tool connections.** Each ERPNext user gets their own Paperclip connection and API key. Airtight, operationally miserable past ~5 people.

---

## Deferred: escape hatch to architecture B

**Status settled 2026-08-06 after reading the Paperclip server source.** Board chat was briefly adopted as the transport and then rejected: it is a *board concierge* that spawns a local `claude` CLI, not the CEO, and it requires `deploymentMode: local_trusted` (loopback-only) because it lends the server's shell to the requester. Unusable on a publicly-reachable authenticated instance, and undesirable even if forced.

Chat therefore uses a standing Issue plus comments. The latency objection is **much weaker than feared**: commenting enqueues an agent wake (`wakeReason: "issue_commented"`), so a reply costs one agent run, not scheduler lag.

Architecture B stays parked, and becomes worth revisiting only if a full Claude Code run per message proves too slow for quick read-only questions in daily use. In that case: run a lightweight agent loop inside Frappe against LiteLLM for chat responsiveness, while still routing *every tool call* through Paperclip's tool gateway so policy, approval, audit and cost tracking remain identical for chat and for routines. The MCP server built in spec 1 is unchanged and fully reused — this is an additive change, not a rewrite.

The property that must never be traded away for speed: **exactly one governed chokepoint through which the agent can touch the business.**

---

## Deferred: autonomy beyond "proactive advisor"

The user's stated ambition is cofounder / director / co-CEO / co-CTO. The autonomy level chosen for now is **proactive advisor — agent proposes, human approves**, which needs no governance framework.

The next rungs, if ever wanted, each require real work *before* code:

- **Delegated authority with a mandate** — a written scope of what the agent may execute alone (e.g. draft POs under a value threshold, bank reconciliations above a confidence bar) versus what escalates. Paperclip already provides the machinery: `budgets` (per agent and per company), budget policies, `trust-rules`, and `tools/policy/test`.
- **Full co-executive** — the agent holds ERPNext roles, signs off documents, is accountable for KPIs. Needs governance, spending limits and legal consideration well ahead of implementation.

---

## Smaller items

- **Voice I/O.** Kokoro TTS (`nuc.lan:18880`), Whisper (`nuc.lan:19000`) and an Indic realtime voice endpoint are already on the LiteLLM gateway. A voice-driven cofounder is cheap to add once chat works.
- **Adapter choice constrains the model.** Paperclip agents are external CLI runtimes selected by `adapterType`: `["process","http","claude_local","codex_local","cursor_cloud","gemini_local","grok_local","hermes_gateway","hermes_local","opencode_local","pi_local","cursor","openclaw_gateway"]`. Running the CEO on the local LiteLLM/Ollama gateway requires `opencode_local` or a custom `http`/`process` adapter — `claude_local` and `gemini_local` bind to those vendors instead. Worth a spike before committing.
- **Ollama credit for K3.** `kimi-k3:cloud` currently returns *"extra usage balance is empty"*. Until topped up, `kimi-k2.6:cloud` is the fallback and was verified to do correct tool calling on 2026-08-06. Only relevant if the adapter routes through the local gateway.
- **Data egress.** K3 and K2.6 are both **Ollama Cloud**, so business data leaves the network — accepted by the user, but revisit if the local stack ever gains a competent tool-calling model. `gpt-oss:20b`, `qwen3:14b` and `mistral-small:24b` are the genuinely local options.
- **Built-in agents and routines.** `enableBuiltInAgents` gates a set of pre-provisioned agents with managed routines (`built-in-agents/{key}/provision|reconcile|reset|status` and `.../routines/{routineKey}/enable|disable|run`). Worth inspecting before hand-authoring the spec-3 beats — some may already exist.
- **Paperclip org modelling.** `role` (ceo/cto/cmo/cfo/…), `reportsTo`, `hire_agent`, `approve_ceo_strategy`, `request_board_approval`, per-agent `budgetMonthlyCents`. If the company ever grows past one agent, this is the hierarchy to use rather than inventing one.
- **Narrow the Postgres role.** `erpnext_db_user` is a Postgres **superuser** — more privilege than Frappe requires. Unrelated to this project but worth fixing.
- **Domain-specific tools.** Considered and not chosen for v1: purpose-built tools for high-traffic flows (quotation from opportunity, bank line reconciliation, stock entry posting). Add only if the generic tools prove unreliable on those paths.
- **Paperclip Skill for ERPNext.** A versioned, test-runnable skill holding ERPNext procedural knowledge, living beside the agent. Complements spec 2 rather than replacing it.

---

## Still outstanding from the previous cycle (unrelated to AI)

Carried forward so it is not lost behind this project:

- **TaskPilot Settings remain unconfigured** — no workspace slug or API key supplied, so the Projects module runs in graceful disabled mode. The live TaskPilot smoke test has never run. First question it must answer: **does `list_projects` return archived projects?** (migration idempotency for Completed projects depends on the answer).

---

## `WRITE_FORBIDDEN_DOCTYPES` is structurally the wrong shape (interim measure)

**Recorded 2026-08-07, during the pre-merge review fix wave that added the scheduler-armed-auto-submit doctypes (Subscription, Auto Repeat, Process Statement Of Accounts, Email Campaign, Notification, Auto Email Report) plus the permission/global-config doctypes to `erpnext/ai/scoping.py`'s deny-list.**

The invariant this whole feature depends on is: **no privileged effect without a call to a gated tool name** (`submit_document`/`cancel_document`/`delete_document`/`call_method`). That property belongs to what a doctype's controller and the scheduler *do* with the fields a free-tier `create_document`/`update_document` call can set — not to the doctype's name. A denylist keyed on name can only ever enumerate doctypes someone has already thought to check. Every entry added in this pass (and the two families added in the previous review round before it) was found by a human reading `hooks.py`'s scheduler jobs and imagining the attack, one doctype at a time. There is no reason to believe that process is exhaustive, and every ERPNext release can add a new scheduled job or a new "submit on X" checkbox to a doctype nobody has vetted yet — the deny-list does not get safer over time, it only gets less wrong for the doctypes someone remembered to look at.

**The correct fix is an allowlist**: a vetted list of business doctypes `create_document`/`update_document` may touch at all, each one checked (once, deliberately) for exactly this property — no field on it, in combination with any scheduler job or hook shipped by frappe or erpnext, can cause a submit/cancel/delete/mass-send effect without the call passing through a gated tool name. Anything not on that list is refused by construction, including doctypes nobody has thought about yet, which is the property a denylist can never have.

**Not implemented now.** This section exists so the shape of the current fix is not mistaken for the real fix. The denylist added in this pass is a legitimate interim measure — it closes every concretely-identified hole — but it is not the invariant; it is a best-effort approximation of it that requires a human to keep finding the next hole.
