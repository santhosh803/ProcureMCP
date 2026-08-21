"""Celery tasks: policy embedding, vendor scoring, approval notifications."""

import json
import logging
from urllib import error as urllib_error
from urllib import request as urllib_request

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _post_webhook(url: str, payload: dict, timeout: float = 10.0):
    """POST a JSON payload to ``url``. Returns the HTTP status code or None."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        return resp.status


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

    If ``NOTIFICATION_WEBHOOK_URL`` is set, POSTs a JSON payload compatible with
    generic incoming webhooks (Slack, Teams, Zapier, custom endpoints). When
    unset, falls back to structured logging so local development stays offline.
    """
    from .models import ApprovalRequest

    try:
        approval = ApprovalRequest.objects.get(pk=approval_id)
    except ApprovalRequest.DoesNotExist:
        return {"approval_id": approval_id, "status": "missing"}

    payload = {
        "event": "approval.pending",
        "approval_id": approval.id,
        "tier": approval.approver_tier,
        "assigned_to": approval.assigned_to,
        "entity_type": approval.entity_type,
        "entity_id": approval.entity_id,
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
        "text": (
            f"Procurement approval required — {approval.approver_tier} tier: "
            f"{approval.entity_type} {approval.entity_id} "
            f"(assigned to {approval.assigned_to or 'unassigned'})"
        ),
    }

    webhook_url = getattr(settings, "NOTIFICATION_WEBHOOK_URL", "") or ""
    if webhook_url:
        try:
            status_code = _post_webhook(webhook_url, payload)
            logger.info(
                "[approval-notification] webhook posted status=%s approval=%s",
                status_code,
                approval_id,
            )
            return {
                "approval_id": approval_id,
                "status": "notified",
                "channel": "webhook",
                "http_status": status_code,
                "assigned_to": approval.assigned_to,
            }
        except (urllib_error.URLError, TimeoutError, OSError) as exc:
            logger.warning(
                "[approval-notification] webhook post failed approval=%s error=%s",
                approval_id,
                exc,
            )
            return {
                "approval_id": approval_id,
                "status": "webhook_failed",
                "assigned_to": approval.assigned_to,
                "error": str(exc),
            }

    logger.info(
        "[approval-notification] tier=%s assigned_to=%s entity=%s:%s",
        approval.approver_tier,
        approval.assigned_to,
        approval.entity_type,
        approval.entity_id,
    )
    return {
        "approval_id": approval_id,
        "status": "notified",
        "channel": "log",
        "assigned_to": approval.assigned_to,
    }
