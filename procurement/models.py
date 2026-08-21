"""Core procurement domain models.

The schema is ERP-inspired but vendor-neutral, covering the full Procure-to-Pay
lifecycle: material and vendor master data, requisitions, purchase orders, goods
receipts, invoices, multi-tier approvals, embedded policy documents for RAG, and
an immutable audit ledger.
"""

import uuid

from django.db import models
from django.utils import timezone
from django_fsm import RETURN_VALUE, FSMField, transition
from pgvector.django import HnswIndex, VectorField

from .state_machines import resolve_receipt_state


def _uuid_str() -> str:
    """Return a UUID4 as a string for human-readable document numbers."""
    return str(uuid.uuid4())


class Material(models.Model):
    """Master data for all procurable materials/items."""

    material_number = models.CharField(max_length=50, unique=True)  # e.g. MAT-STL-BLT-M8-001
    description = models.CharField(max_length=255)
    material_group = models.CharField(max_length=100)  # raw_materials, indirect, services, capex
    unit_of_measure = models.CharField(max_length=20)  # EA, KG, L, HR
    base_price = models.DecimalField(max_digits=12, decimal_places=2)
    lead_time_days = models.IntegerField(default=7)
    reorder_point = models.IntegerField(default=0)
    stock_qty = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["material_number"]

    def __str__(self):
        return f"{self.material_number} — {self.description}"


class VendorMaster(models.Model):
    """Approved vendor master data with performance scoring."""

    class Status(models.TextChoices):
        APPROVED = "approved"
        UNDER_REVIEW = "under_review"
        BLACKLISTED = "blacklisted"

    vendor_code = models.CharField(max_length=20, unique=True)  # VEN-00001
    name = models.CharField(max_length=255)
    country = models.CharField(max_length=100)
    payment_terms = models.CharField(max_length=50, default="NET30")  # NET30, NET60, 2/10 NET30
    material_groups = models.JSONField(default=list)  # Categories this vendor supplies
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPROVED)
    on_time_delivery_pct = models.FloatField(default=0.0)
    quality_rating = models.FloatField(default=0.0)  # 0.0 - 5.0
    price_competitiveness = models.FloatField(default=0.0)  # 0.0 - 5.0
    overall_score = models.FloatField(default=0.0)  # Computed weighted score
    total_orders = models.IntegerField(default=0)
    last_evaluated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["vendor_code"]

    def __str__(self):
        return f"{self.vendor_code} — {self.name}"


class PurchaseRequisition(models.Model):
    """Initial request for materials, created by a requester."""

    class Status(models.TextChoices):
        DRAFT = "draft"
        SUBMITTED = "submitted"
        APPROVED = "approved"
        REJECTED = "rejected"
        CONVERTED_TO_PO = "converted_to_po"

    pr_number = models.CharField(max_length=50, unique=True, default=_uuid_str)
    requester = models.CharField(max_length=100)  # employee email or ID
    cost_center = models.CharField(max_length=50)
    justification = models.TextField()
    total_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PR {self.pr_number} ({self.get_status_display()})"


class PRLineItem(models.Model):
    """Individual material line on a PR."""

    pr = models.ForeignKey(
        PurchaseRequisition, on_delete=models.CASCADE, related_name="line_items"
    )
    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    estimated_unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    delivery_date_needed = models.DateField()

    def __str__(self):
        return f"{self.quantity} x {self.material.material_number}"


class PurchaseOrder(models.Model):
    """Formal order to a vendor, generated from an approved PR."""

    class Status(models.TextChoices):
        DRAFT = "draft"
        PENDING_APPROVAL = "pending_approval"
        APPROVED = "approved"
        REJECTED = "rejected"
        SENT_TO_VENDOR = "sent_to_vendor"
        PARTIALLY_RECEIVED = "partially_received"
        FULLY_RECEIVED = "fully_received"
        INVOICED = "invoiced"
        CLOSED = "closed"
        CANCELLED = "cancelled"

    po_number = models.CharField(max_length=50, unique=True, default=_uuid_str)
    source_pr = models.ForeignKey(
        PurchaseRequisition,
        on_delete=models.PROTECT,
        related_name="purchase_orders",
        null=True,
        blank=True,
    )
    vendor = models.ForeignKey(
        VendorMaster, on_delete=models.PROTECT, related_name="purchase_orders"
    )
    total_value = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="USD")
    # FSMField governs the lifecycle; transitions are the methods below. It is
    # left unprotected so master-data tooling (seeding, imports) can set states
    # directly, while application flows go through the guarded transitions.
    status = FSMField(
        max_length=30, choices=Status.choices, default=Status.DRAFT, protected=False
    )
    is_sole_source = models.BooleanField(default=False)
    sole_source_justification = models.TextField(blank=True)
    policy_compliance_notes = models.JSONField(
        default=list, blank=True
    )  # Citations from policy RAG
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PO {self.po_number} ({self.get_status_display()})"

    # -- Lifecycle transitions (django-fsm) ---------------------------------

    @transition(field=status, source=Status.DRAFT, target=Status.PENDING_APPROVAL)
    def submit_for_approval(self):
        """Submit a draft PO and open the required approval request(s)."""
        from .approval_engine import route_approval

        return route_approval(self, self.total_value, self.is_sole_source)

    @transition(
        field=status,
        source=Status.PENDING_APPROVAL,
        target=Status.APPROVED,
        permission="procurement.change_purchaseorder",
    )
    def approve(self):
        self.approved_at = timezone.now()

    @transition(field=status, source=Status.PENDING_APPROVAL, target=Status.REJECTED)
    def reject(self):
        pass

    @transition(field=status, source=Status.APPROVED, target=Status.SENT_TO_VENDOR)
    def send_to_vendor(self):
        self.sent_at = timezone.now()

    @transition(
        field=status,
        source=[Status.SENT_TO_VENDOR, Status.PARTIALLY_RECEIVED],
        target=RETURN_VALUE(Status.PARTIALLY_RECEIVED, Status.FULLY_RECEIVED),
    )
    def record_receipt(self):
        """Advance to partially/fully received based on line-item quantities."""
        return resolve_receipt_state(self)

    @transition(field=status, source=Status.FULLY_RECEIVED, target=Status.INVOICED)
    def mark_invoiced(self):
        pass

    @transition(field=status, source=Status.INVOICED, target=Status.CLOSED)
    def close(self):
        pass

    @transition(
        field=status,
        source=[
            Status.DRAFT,
            Status.PENDING_APPROVAL,
            Status.APPROVED,
            Status.SENT_TO_VENDOR,
        ],
        target=Status.CANCELLED,
    )
    def cancel(self):
        pass


class POLineItem(models.Model):
    """Individual material line on a PO with agreed unit price."""

    po = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="line_items"
    )
    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    quantity_ordered = models.DecimalField(max_digits=12, decimal_places=2)
    quantity_received = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    delivery_date = models.DateField()

    def __str__(self):
        return f"{self.quantity_ordered} x {self.material.material_number}"


class GoodsReceipt(models.Model):
    """Record of goods received against a PO."""

    class QualityStatus(models.TextChoices):
        ACCEPTED = "accepted"
        REJECTED = "rejected"
        PARTIAL = "partial"

    gr_number = models.CharField(max_length=50, unique=True, default=_uuid_str)
    po = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, related_name="goods_receipts"
    )
    received_by = models.CharField(max_length=100)
    quality_status = models.CharField(max_length=20, choices=QualityStatus.choices)
    quality_notes = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"GR {self.gr_number}"


class GRLineItem(models.Model):
    """Per-line quantities on a goods receipt."""

    gr = models.ForeignKey(
        GoodsReceipt, on_delete=models.CASCADE, related_name="line_items"
    )
    po_line = models.ForeignKey(POLineItem, on_delete=models.PROTECT)
    quantity_received = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.quantity_received} received"


class Invoice(models.Model):
    """Vendor invoice matched against PO + GR (three-way match)."""

    class MatchStatus(models.TextChoices):
        PENDING_MATCH = "pending_match"
        MATCHED = "matched"
        DISCREPANCY = "discrepancy"
        APPROVED_FOR_PAYMENT = "approved_for_payment"
        PAID = "paid"

    invoice_number = models.CharField(max_length=50, unique=True)
    po = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, related_name="invoices"
    )
    vendor_invoice_ref = models.CharField(max_length=100)
    invoice_amount = models.DecimalField(max_digits=12, decimal_places=2)
    match_status = models.CharField(
        max_length=30,
        choices=MatchStatus.choices,
        default=MatchStatus.PENDING_MATCH,
    )
    match_discrepancies = models.JSONField(default=list, blank=True)
    invoice_date = models.DateField()
    due_date = models.DateField()

    class Meta:
        ordering = ["-invoice_date"]

    def __str__(self):
        return f"Invoice {self.invoice_number}"


class ApprovalRequest(models.Model):
    """HITL approval routing for POs and PRs by tier."""

    class ApproverTier(models.TextChoices):
        MANAGER = "manager"  # < $10K
        FINANCE = "finance"  # $10K - $50K
        CFO = "cfo"  # > $50K
        SOLE_SOURCE_COMMITTEE = "sole_source_committee"  # Parallel path

    class Decision(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        ESCALATED = "escalated"

    request_id = models.CharField(max_length=50, unique=True, default=_uuid_str)
    entity_type = models.CharField(max_length=50)  # "purchase_order" | "purchase_requisition"
    entity_id = models.CharField(max_length=50)
    approver_tier = models.CharField(max_length=30, choices=ApproverTier.choices)
    assigned_to = models.CharField(max_length=100, blank=True)
    decision = models.CharField(
        max_length=20, choices=Decision.choices, default=Decision.PENDING
    )
    decision_reason = models.TextField(blank=True)
    ai_risk_summary = models.TextField(blank=True)  # Generated risk brief for the approver
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Approval {self.request_id} [{self.approver_tier}] {self.decision}"


class PolicyDocument(models.Model):
    """Procurement policy documents embedded for RAG retrieval."""

    class PolicyType(models.TextChoices):
        SPENDING_LIMIT = "spending_limit"
        APPROVED_VENDOR = "approved_vendor"
        CATEGORY_RULE = "category_rule"
        SOLE_SOURCE = "sole_source"
        COMPLIANCE = "compliance"
        GENERAL = "general"

    title = models.CharField(max_length=255)
    policy_type = models.CharField(max_length=30, choices=PolicyType.choices)
    content = models.TextField()
    embedding = VectorField(
        dimensions=768, null=True, blank=True
    )  # text-embedding-004 dimensions
    version = models.CharField(max_length=20, default="1.0")
    effective_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["policy_type", "title"]
        indexes = [
            HnswIndex(
                name="policy_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            )
        ]

    def __str__(self):
        return f"{self.title} ({self.get_policy_type_display()})"


class AuditLedger(models.Model):
    """Immutable audit trail for every procurement decision."""

    event_id = models.CharField(max_length=50, unique=True, default=_uuid_str)
    entity_type = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=50)
    action = models.CharField(max_length=100)  # created, approved, sent, received, matched, paid
    actor = models.CharField(max_length=100)  # "agent:langgraph" | "agent:claude_desktop" | email
    context_json = models.JSONField(default=dict)
    policy_citations = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} on {self.entity_type}:{self.entity_id}"


class AgentSession(models.Model):
    """Distributed registry for LangGraph orchestrator sessions.

    The LangGraph checkpointer stores the run's state; this table stores the
    lightweight status and ownership metadata so any web/worker process can
    look up whether a session is idle, running, or awaiting_approval.
    """

    class Status(models.TextChoices):
        IDLE = "idle"
        RUNNING = "running"
        AWAITING_APPROVAL = "awaiting_approval"
        FAILED = "failed"

    session_id = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="agent_sessions",
    )
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.IDLE
    )
    last_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_activity"]

    def __str__(self):
        return f"{self.session_id} [{self.status}]"
