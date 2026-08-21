"""Multi-tier approval routing.

Routes an entity to an approver tier based on monetary value, and — for
sole-source purchases — additionally opens a parallel sole-source committee
review. Approver assignments come from a config-driven pool.
"""

from decimal import Decimal

from .models import ApprovalRequest

# Tier thresholds (USD). Value < MANAGER_CEILING -> manager, etc.
MANAGER_CEILING = Decimal("10000")
FINANCE_CEILING = Decimal("50000")

# Config-driven approver pool. In production these map to directory groups.
APPROVER_POOL = {
    ApprovalRequest.ApproverTier.MANAGER: "manager@procuremcp.example",
    ApprovalRequest.ApproverTier.FINANCE: "finance@procuremcp.example",
    ApprovalRequest.ApproverTier.CFO: "cfo@procuremcp.example",
    ApprovalRequest.ApproverTier.SOLE_SOURCE_COMMITTEE: "committee@procuremcp.example",
}


def tier_for_value(value) -> str:
    """Map a monetary value to the required approver tier."""
    value = Decimal(str(value))
    if value < MANAGER_CEILING:
        return ApprovalRequest.ApproverTier.MANAGER
    if value < FINANCE_CEILING:
        return ApprovalRequest.ApproverTier.FINANCE
    return ApprovalRequest.ApproverTier.CFO


def _risk_summary(entity_type, value, tier, is_sole_source) -> str:
    parts = [
        f"{entity_type.replace('_', ' ').title()} valued at USD {Decimal(str(value)):,.2f}",
        f"routed to {tier} tier.",
    ]
    if is_sole_source:
        parts.append(
            "Sole-source purchase — competitive bidding bypassed; parallel "
            "committee review required."
        )
    else:
        parts.append("Standard competitive purchase.")
    return " ".join(parts)


def route_approval(entity, value, is_sole_source=False):
    """Create approval request(s) for an entity and return them.

    Determines ``entity_type``/``entity_id`` from the model instance, opens a
    value-based tier approval, and — when sole-source — a parallel committee
    approval. Returns a list of created :class:`ApprovalRequest` objects.
    """
    from .models import PurchaseOrder, PurchaseRequisition

    if isinstance(entity, PurchaseOrder):
        entity_type, entity_id = "purchase_order", entity.po_number
    elif isinstance(entity, PurchaseRequisition):
        entity_type, entity_id = "purchase_requisition", entity.pr_number
    else:
        entity_type = getattr(entity, "entity_type", "unknown")
        entity_id = str(getattr(entity, "pk", entity))

    tier = tier_for_value(value)
    created = [
        ApprovalRequest.objects.create(
            entity_type=entity_type,
            entity_id=entity_id,
            approver_tier=tier,
            assigned_to=APPROVER_POOL[tier],
            ai_risk_summary=_risk_summary(entity_type, value, tier, is_sole_source),
        )
    ]

    if is_sole_source:
        committee = ApprovalRequest.ApproverTier.SOLE_SOURCE_COMMITTEE
        created.append(
            ApprovalRequest.objects.create(
                entity_type=entity_type,
                entity_id=entity_id,
                approver_tier=committee,
                assigned_to=APPROVER_POOL[committee],
                ai_risk_summary=(
                    "Parallel sole-source committee review: verify justification "
                    "and absence of viable competitive alternatives."
                ),
            )
        )

    return created


def apply_decision(entity_type, entity_id, decision, reason=""):
    """Record a human approval decision and advance the entity's state.

    Marks every still-pending :class:`ApprovalRequest` for the entity with the
    decision (a single chat approval resolves the whole gate, including the
    parallel sole-source committee request), then advances a
    :class:`PurchaseOrder` through its FSM (``approve``/``reject``). Post-save
    signals write the audit-ledger entries.

    Returns a summary dict; never raises for an already-resolved or
    wrong-state entity — it reports the current state instead.
    """
    from django.utils import timezone
    from django_fsm import TransitionNotAllowed

    from .models import PurchaseOrder

    normalized = (decision or "").strip().lower()
    if normalized not in {ApprovalRequest.Decision.APPROVED, ApprovalRequest.Decision.REJECTED}:
        return {"error": f"Unsupported decision: {decision!r}"}

    pending = ApprovalRequest.objects.filter(
        entity_type=entity_type,
        entity_id=entity_id,
        decision=ApprovalRequest.Decision.PENDING,
    )
    updated = 0
    for req in list(pending):
        req.decision = normalized
        req.decision_reason = reason or ""
        req.decided_at = timezone.now()
        req.save(update_fields=["decision", "decision_reason", "decided_at"])
        updated += 1

    entity_status = None
    transitioned = False
    if entity_type == "purchase_order":
        po = PurchaseOrder.objects.filter(po_number=entity_id).first()
        if po is not None:
            if po.status == PurchaseOrder.Status.PENDING_APPROVAL:
                try:
                    if normalized == ApprovalRequest.Decision.APPROVED:
                        po.approve()
                    else:
                        po.reject()
                    po.save()
                    transitioned = True
                except TransitionNotAllowed:
                    pass
            entity_status = po.status

    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "decision": normalized,
        "approvals_updated": updated,
        "entity_status": entity_status,
        "transitioned": transitioned,
    }
