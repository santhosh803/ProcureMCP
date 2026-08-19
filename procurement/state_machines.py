"""Purchase-order lifecycle state machine helpers.

The transition methods themselves live on :class:`PurchaseOrder` (django-fsm
requires ``@transition`` decorators on model methods). This module documents the
canonical transition graph and provides the receipt-state resolver used by the
``record_receipt`` transition, keeping that branching logic in one testable place.
"""

from decimal import Decimal

# Canonical lifecycle transition graph: state -> reachable states.
PO_TRANSITION_GRAPH = {
    "draft": ["pending_approval", "cancelled"],
    "pending_approval": ["approved", "rejected", "cancelled"],
    "approved": ["sent_to_vendor", "cancelled"],
    "sent_to_vendor": ["partially_received", "fully_received", "cancelled"],
    "partially_received": ["partially_received", "fully_received"],
    "fully_received": ["invoiced"],
    "invoiced": ["closed"],
    "closed": [],
    "rejected": [],
    "cancelled": [],
}


def resolve_receipt_state(po) -> str:
    """Return the target PO state after a goods receipt.

    Fully received when every line's received quantity has caught up to the
    ordered quantity; otherwise partially received.
    """
    lines = list(po.line_items.all())
    if not lines:
        return "partially_received"
    fully = all(
        (line.quantity_received or Decimal("0")) >= line.quantity_ordered
        for line in lines
    )
    return "fully_received" if fully else "partially_received"
