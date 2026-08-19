"""Signal handlers that keep the immutable audit ledger in sync.

Every tracked procurement entity writes an AuditLedger entry on save. New policy
documents trigger the embedding pipeline (wired to Celery in a later phase; a
no-op stub keeps this decoupled until then).
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (
    ApprovalRequest,
    AuditLedger,
    GoodsReceipt,
    Invoice,
    PolicyDocument,
    PurchaseOrder,
    PurchaseRequisition,
)

logger = logging.getLogger(__name__)


def _record(entity_type, entity_id, action, actor="system", context=None, citations=None):
    AuditLedger.objects.create(
        entity_type=entity_type,
        entity_id=str(entity_id),
        action=action,
        actor=actor,
        context_json=context or {},
        policy_citations=citations or [],
    )


@receiver(post_save, sender=PurchaseRequisition)
def audit_purchase_requisition(sender, instance, created, **kwargs):
    _record(
        "purchase_requisition",
        instance.pr_number,
        "created" if created else f"updated:{instance.status}",
        actor=instance.requester or "system",
        context={"status": instance.status, "total_value": str(instance.total_value)},
    )


@receiver(post_save, sender=PurchaseOrder)
def audit_purchase_order(sender, instance, created, **kwargs):
    _record(
        "purchase_order",
        instance.po_number,
        "created" if created else f"updated:{instance.status}",
        context={
            "status": instance.status,
            "total_value": str(instance.total_value),
            "vendor": instance.vendor.vendor_code,
            "is_sole_source": instance.is_sole_source,
        },
        citations=instance.policy_compliance_notes or [],
    )


@receiver(post_save, sender=GoodsReceipt)
def audit_goods_receipt(sender, instance, created, **kwargs):
    _record(
        "goods_receipt",
        instance.gr_number,
        "received" if created else f"updated:{instance.quality_status}",
        actor=instance.received_by or "system",
        context={"po": instance.po.po_number, "quality_status": instance.quality_status},
    )


@receiver(post_save, sender=Invoice)
def audit_invoice(sender, instance, created, **kwargs):
    _record(
        "invoice",
        instance.invoice_number,
        "created" if created else f"match:{instance.match_status}",
        context={
            "po": instance.po.po_number,
            "amount": str(instance.invoice_amount),
            "match_status": instance.match_status,
        },
    )


@receiver(post_save, sender=ApprovalRequest)
def audit_approval_request(sender, instance, created, **kwargs):
    _record(
        "approval_request",
        instance.request_id,
        "requested" if created else f"decided:{instance.decision}",
        actor=instance.assigned_to or "system",
        context={
            "entity": f"{instance.entity_type}:{instance.entity_id}",
            "tier": instance.approver_tier,
            "decision": instance.decision,
        },
    )


@receiver(post_save, sender=PolicyDocument)
def embed_policy_on_create(sender, instance, created, **kwargs):
    """Queue a newly created policy document for embedding.

    The Celery-backed embedding task is wired in Phase 4. Until then this stays a
    best-effort no-op so policy creation never fails when the pipeline is absent.
    """
    if not created:
        return
    try:
        from .tasks import embed_policy_document_task

        embed_policy_document_task.delay(instance.id)
    except Exception:  # noqa: BLE001 - embedding pipeline not yet available
        logger.debug("Embedding pipeline unavailable; policy %s not queued.", instance.id)
