"""Celery tasks: policy embedding, vendor scoring, approval notifications."""

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def _embed_policy(policy) -> bool:
    """Compute and store the embedding for a policy document. Returns success."""
    from .embeddings import embed_text

    text = f"{policy.title}\n\n{policy.content}"
    vector = embed_text(text)
    policy.embedding = vector
    policy.save(update_fields=["embedding"])
    return True


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def embed_policy_document_task(self, policy_id):
    """Embed a single PolicyDocument via Vertex AI text-embedding-004."""
    from .models import PolicyDocument

    try:
        policy = PolicyDocument.objects.get(pk=policy_id)
    except PolicyDocument.DoesNotExist:
        logger.warning("PolicyDocument %s no longer exists; skipping embed.", policy_id)
        return {"policy_id": policy_id, "status": "missing"}

    try:
        _embed_policy(policy)
    except Exception as exc:  # noqa: BLE001 - transient API/network errors are retried
        logger.exception("Embedding failed for policy %s", policy_id)
        raise self.retry(exc=exc)

    return {"policy_id": policy_id, "status": "embedded", "dimensions": len(policy.embedding)}


@shared_task
def compute_vendor_score_task(vendor_id):
    """Recompute a vendor's weighted overall score from its performance metrics."""
    from .models import VendorMaster

    try:
        vendor = VendorMaster.objects.get(pk=vendor_id)
    except VendorMaster.DoesNotExist:
        return {"vendor_id": vendor_id, "status": "missing"}

    vendor.overall_score = round(
        (vendor.on_time_delivery_pct / 100.0) * 5.0 * 0.4
        + vendor.quality_rating * 0.35
        + vendor.price_competitiveness * 0.25,
        2,
    )
    vendor.last_evaluated_at = timezone.now()
    vendor.save(update_fields=["overall_score", "last_evaluated_at"])
    return {"vendor_id": vendor_id, "status": "scored", "overall_score": vendor.overall_score}


@shared_task
def send_approval_notification_task(approval_id):
    """Notify the assigned approver of a pending request.

    Placeholder for a real email/notification integration — logs to the console.
    """
    from .models import ApprovalRequest

    try:
        approval = ApprovalRequest.objects.get(pk=approval_id)
    except ApprovalRequest.DoesNotExist:
        return {"approval_id": approval_id, "status": "missing"}

    logger.info(
        "[approval-notification] tier=%s assigned_to=%s entity=%s:%s",
        approval.approver_tier,
        approval.assigned_to,
        approval.entity_type,
        approval.entity_id,
    )
    return {"approval_id": approval_id, "status": "notified", "assigned_to": approval.assigned_to}
