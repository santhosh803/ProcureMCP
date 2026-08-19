# Architecture

ProcureMCP separates the procurement **domain** from the **agent** layer through
an MCP tool boundary.

## Layers

1. **Django domain** (`procurement/`) — models, business rules, the approval
   engine, the PO state machine, DRF APIs, an immutable audit ledger, and Celery
   tasks. This is the single source of truth.
2. **MCP tool layer** (`mcp_server/`) — ten atomic Procure-to-Pay operations that
   read and write through the Django ORM. Exposed over stdio and SSE. Framework
   agnostic: no agent-specific logic leaks into the tools.
3. **Agent layer** (`agent/`) — an internal LangGraph orchestrator using the
   RAG-in-the-loop pattern. Any external MCP client (e.g. Claude Desktop) consumes
   the exact same tools, demonstrating framework independence.
4. **Operator UI** — the customized Django Admin plus a minimal Tailwind chat
   page that streams the agent's reasoning.

## Data flow for a purchase

```
User request
  → retrieve_policy_context (pgvector RAG)
  → reason (Gemini 2.5 Pro)
  → act (MCP tool: material lookup, vendor search/eval, PR, PO)
  → policy compliance check (RAG citations attached to the PO)
  → approval routing (value + sole-source → ApprovalRequest)
  → HITL pause (hitl_pending) → human decision → PO state transition
```

Every persisted change to a tracked entity writes an `AuditLedger` row via a
`post_save` signal, producing an immutable, queryable trail.

## Async pipeline

`post_save` on a new `PolicyDocument` enqueues `embed_policy_document_task`.
Celery runs on Upstash Redis shared with another project; the mandatory
`global_keyprefix: 'procuremcp:'` namespaces every key.
