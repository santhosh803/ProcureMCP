# Approval Workflows

## Tier routing

Routing is a pure function of PO value (`procurement/approval_engine.py`):

| Value | Tier |
|---|---|
| `< $10,000` | `manager` |
| `$10,000 – $49,999.99` | `finance` |
| `≥ $50,000` | `cfo` |

Thresholds are boundary-exact: exactly `$10,000` routes to finance; exactly
`$50,000` routes to CFO.

## Sole-source path

When a PO is flagged `is_sole_source`, routing creates the value-based
ApprovalRequest **and** a parallel `sole_source_committee` request. Both must be
resolved; a single rejection rejects the PO.

## PO lifecycle state machine

Implemented with `django-fsm` on `PurchaseOrder.status`:

```
draft ──submit_for_approval()──▶ pending_approval
pending_approval ──approve()──▶ approved       (permission required)
pending_approval ──reject()───▶ rejected
approved ──send_to_vendor()──▶ sent_to_vendor
sent_to_vendor ──record_receipt()──▶ partially_received | fully_received
partially_received ──record_receipt()──▶ fully_received
fully_received ──mark_invoiced()──▶ invoiced
invoiced ──close()──▶ closed
draft|pending_approval|approved|sent_to_vendor ──cancel()──▶ cancelled
```

`submit_for_approval()` automatically invokes the routing engine. Invalid
transitions raise `TransitionNotAllowed`.

## Human-in-the-loop

Approval-gated tools return `hitl_pending`. In the LangGraph agent, the
`hitl_check` node calls `interrupt()` to pause the run; the
`POST /api/agent/approve/` endpoint resumes it with the approver's decision,
which is folded back into the conversation and the underlying PO state.

## Admin actions

The `ApprovalRequest` admin offers bulk **Approve** / **Reject** actions that
cascade to the underlying purchase order: a PO is approved once no requests for
it remain pending, and rejected on any rejection.
