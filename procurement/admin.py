"""Customized Django Admin — the operator interface for ProcureMCP.

The admin is deliberately rich: colour-coded status badges, currency
formatting, vendor scorecards, inline line items, related-record panels on the
purchase-order detail view, an approval dashboard with one-click decisions, and
a read-only audit ledger with a JSON viewer.
"""

import json
from decimal import Decimal

from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html
from django_fsm import TransitionNotAllowed

from .models import (
    AgentSession,
    ApprovalRequest,
    AuditLedger,
    GoodsReceipt,
    GRLineItem,
    Invoice,
    Material,
    POLineItem,
    PolicyDocument,
    PRLineItem,
    PurchaseOrder,
    PurchaseRequisition,
    VendorMaster,
)

admin.site.site_header = "ProcureMCP — Enterprise Procurement Platform"
admin.site.site_title = "ProcureMCP Admin"
admin.site.index_title = "Procurement Operations"


# --- Shared helpers ----------------------------------------------------------

def _badge(label: str, color: str) -> str:
    return format_html(
        '<span style="display:inline-block;padding:2px 9px;border-radius:10px;'
        'font-size:11px;font-weight:600;color:#fff;background:{};">{}</span>',
        color,
        label,
    )


def _money(value, currency: str = "USD") -> str:
    if value is None:
        return "—"
    return f"{currency} {Decimal(value):,.2f}"


PO_STATUS_COLORS = {
    "draft": "#6b7280",
    "pending_approval": "#d97706",
    "approved": "#2563eb",
    "rejected": "#dc2626",
    "sent_to_vendor": "#7c3aed",
    "partially_received": "#0891b2",
    "fully_received": "#0d9488",
    "invoiced": "#4f46e5",
    "closed": "#16a34a",
    "cancelled": "#dc2626",
}

VENDOR_STATUS_COLORS = {
    "approved": "#16a34a",
    "under_review": "#d97706",
    "blacklisted": "#dc2626",
}

APPROVAL_DECISION_COLORS = {
    "pending": "#d97706",
    "approved": "#16a34a",
    "rejected": "#dc2626",
    "escalated": "#7c3aed",
}


def _score_cell(value: float) -> str:
    if value >= 4.0:
        color = "#16a34a"
    elif value >= 2.5:
        color = "#d97706"
    else:
        color = "#dc2626"
    return format_html(
        '<span style="font-weight:600;color:{};">{}</span>', color, f"{value:.2f}"
    )


# --- Inlines -----------------------------------------------------------------

class PRLineItemInline(admin.TabularInline):
    model = PRLineItem
    extra = 0
    autocomplete_fields = ["material"]


class POLineItemInline(admin.TabularInline):
    model = POLineItem
    extra = 0
    autocomplete_fields = ["material"]
    readonly_fields = ["quantity_received"]


class GRLineItemInline(admin.TabularInline):
    model = GRLineItem
    extra = 0


class GoodsReceiptRelatedInline(admin.TabularInline):
    model = GoodsReceipt
    extra = 0
    fields = ["gr_number", "received_by", "quality_status", "received_at"]
    readonly_fields = ["gr_number", "received_by", "quality_status", "received_at"]
    show_change_link = True
    can_delete = False

    def has_add_permission(self, request, obj):
        return False


class InvoiceRelatedInline(admin.TabularInline):
    model = Invoice
    extra = 0
    fields = ["invoice_number", "invoice_amount", "match_status", "due_date"]
    readonly_fields = ["invoice_number", "invoice_amount", "match_status", "due_date"]
    show_change_link = True
    can_delete = False

    def has_add_permission(self, request, obj):
        return False


# --- Material ----------------------------------------------------------------

@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = [
        "material_number",
        "description",
        "material_group",
        "unit_of_measure",
        "formatted_price",
        "stock_qty",
        "reorder_point",
        "stock_health",
    ]
    list_filter = ["material_group", "unit_of_measure"]
    search_fields = ["material_number", "description"]
    ordering = ["material_number"]

    @admin.display(description="Base price")
    def formatted_price(self, obj):
        return _money(obj.base_price)

    @admin.display(description="Stock")
    def stock_health(self, obj):
        if obj.stock_qty <= obj.reorder_point:
            return _badge("REORDER", "#dc2626")
        return _badge("OK", "#16a34a")


# --- Vendor ------------------------------------------------------------------

@admin.register(VendorMaster)
class VendorMasterAdmin(admin.ModelAdmin):
    list_display = [
        "vendor_code",
        "name",
        "country",
        "status_badge",
        "delivery_cell",
        "quality_cell",
        "price_cell",
        "overall_cell",
        "total_orders",
    ]
    list_filter = ["status", "country"]
    search_fields = ["vendor_code", "name"]
    ordering = ["vendor_code"]
    actions = ["recompute_score"]
    readonly_fields = ["overall_score", "last_evaluated_at"]

    @admin.display(description="Status")
    def status_badge(self, obj):
        return _badge(
            obj.get_status_display().upper(),
            VENDOR_STATUS_COLORS.get(obj.status, "#6b7280"),
        )

    @admin.display(description="On-time %")
    def delivery_cell(self, obj):
        return _score_cell(obj.on_time_delivery_pct / 20.0)  # scale 0-100 -> 0-5 for colour

    @admin.display(description="Quality")
    def quality_cell(self, obj):
        return _score_cell(obj.quality_rating)

    @admin.display(description="Price")
    def price_cell(self, obj):
        return _score_cell(obj.price_competitiveness)

    @admin.display(description="Overall")
    def overall_cell(self, obj):
        return _score_cell(obj.overall_score)

    @admin.action(description="Recompute vendor score")
    def recompute_score(self, request, queryset):
        for vendor in queryset:
            vendor.overall_score = round(
                (vendor.on_time_delivery_pct / 100.0) * 5.0 * 0.4
                + vendor.quality_rating * 0.35
                + vendor.price_competitiveness * 0.25,
                2,
            )
            vendor.last_evaluated_at = timezone.now()
            vendor.save(update_fields=["overall_score", "last_evaluated_at"])
        self.message_user(
            request, f"Recomputed score for {queryset.count()} vendor(s).", messages.SUCCESS
        )


# --- Purchase Requisition ----------------------------------------------------

@admin.register(PurchaseRequisition)
class PurchaseRequisitionAdmin(admin.ModelAdmin):
    list_display = [
        "pr_number",
        "requester",
        "cost_center",
        "formatted_value",
        "status_badge",
        "created_at",
    ]
    list_filter = ["status", "cost_center"]
    search_fields = ["pr_number", "requester", "justification"]
    inlines = [PRLineItemInline]
    date_hierarchy = "created_at"

    @admin.display(description="Total value")
    def formatted_value(self, obj):
        return _money(obj.total_value)

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "draft": "#6b7280",
            "submitted": "#d97706",
            "approved": "#16a34a",
            "rejected": "#dc2626",
            "converted_to_po": "#2563eb",
        }
        return _badge(obj.get_status_display().upper(), colors.get(obj.status, "#6b7280"))


# --- Purchase Order ----------------------------------------------------------

@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = [
        "po_number",
        "vendor_name",
        "formatted_value",
        "status_badge",
        "sole_source_flag",
        "days_pending",
        "created_at",
    ]
    list_filter = ["status", "is_sole_source", "vendor__status", "currency"]
    search_fields = ["po_number", "vendor__name", "vendor__vendor_code"]
    autocomplete_fields = ["vendor", "source_pr"]
    inlines = [POLineItemInline, GoodsReceiptRelatedInline, InvoiceRelatedInline]
    actions = ["approve_selected"]
    date_hierarchy = "created_at"
    readonly_fields = ["approved_at", "sent_at", "related_approvals", "audit_trail"]
    fieldsets = (
        (None, {
            "fields": (
                "po_number",
                "source_pr",
                "vendor",
                ("total_value", "currency"),
                "status",
            )
        }),
        ("Sole source", {
            "fields": ("is_sole_source", "sole_source_justification"),
        }),
        ("Policy & approvals", {
            "fields": ("policy_compliance_notes", "related_approvals"),
        }),
        ("Timeline", {
            "fields": ("created_at", "approved_at", "sent_at", "audit_trail"),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if obj is not None:
            ro.append("created_at")
        return ro

    @admin.display(description="Vendor")
    def vendor_name(self, obj):
        return obj.vendor.name

    @admin.display(description="Total value")
    def formatted_value(self, obj):
        return _money(obj.total_value, obj.currency)

    @admin.display(description="Status")
    def status_badge(self, obj):
        return _badge(
            obj.get_status_display().upper(),
            PO_STATUS_COLORS.get(obj.status, "#6b7280"),
        )

    @admin.display(description="Sole source", boolean=True)
    def sole_source_flag(self, obj):
        return obj.is_sole_source

    @admin.display(description="Days pending")
    def days_pending(self, obj):
        if obj.status in {"closed", "cancelled"}:
            return "—"
        delta = (timezone.now() - obj.created_at).days
        color = "#dc2626" if delta > 7 else "#6b7280"
        return format_html('<span style="color:{};">{} d</span>', color, delta)

    @admin.display(description="Approval requests")
    def related_approvals(self, obj):
        approvals = ApprovalRequest.objects.filter(
            entity_type="purchase_order", entity_id=obj.po_number
        )
        if not approvals:
            return "No approval requests."
        rows = []
        for a in approvals:
            rows.append(
                format_html(
                    "<li><b>{}</b> — {} (assigned: {})</li>",
                    a.get_approver_tier_display(),
                    _badge(
                        a.get_decision_display().upper(),
                        APPROVAL_DECISION_COLORS.get(a.decision, "#6b7280"),
                    ),
                    a.assigned_to or "unassigned",
                )
            )
        return format_html("<ul style='margin:0;padding-left:16px;'>{}</ul>", format_html("".join(rows)))

    @admin.display(description="Audit trail")
    def audit_trail(self, obj):
        events = AuditLedger.objects.filter(
            entity_type="purchase_order", entity_id=obj.po_number
        )[:20]
        if not events:
            return "No audit events yet."
        rows = []
        for e in events:
            rows.append(
                format_html(
                    "<li>{:%Y-%m-%d %H:%M} — <b>{}</b> by {}</li>",
                    timezone.localtime(e.created_at),
                    e.action,
                    e.actor,
                )
            )
        return format_html("<ul style='margin:0;padding-left:16px;'>{}</ul>", format_html("".join(rows)))

    @admin.action(description="Approve selected purchase orders")
    def approve_selected(self, request, queryset):
        if not request.user.has_perm("procurement.change_purchaseorder"):
            self.message_user(
                request, "You do not have permission to approve purchase orders.", messages.ERROR
            )
            return
        count = 0
        skipped = 0
        for po in queryset:
            try:
                po.approve()
            except TransitionNotAllowed:
                skipped += 1
                continue
            po.save()
            count += 1
        msg = f"Approved {count} purchase order(s)."
        if skipped:
            msg += f" Skipped {skipped} not in 'pending_approval' state."
        self.message_user(request, msg, messages.SUCCESS)


# --- Goods Receipt -----------------------------------------------------------

@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(admin.ModelAdmin):
    list_display = ["gr_number", "po_link", "received_by", "quality_badge", "received_at"]
    list_filter = ["quality_status"]
    search_fields = ["gr_number", "po__po_number", "received_by"]
    inlines = [GRLineItemInline]
    date_hierarchy = "received_at"

    @admin.display(description="PO")
    def po_link(self, obj):
        return obj.po.po_number

    @admin.display(description="Quality")
    def quality_badge(self, obj):
        colors = {"accepted": "#16a34a", "rejected": "#dc2626", "partial": "#d97706"}
        return _badge(obj.get_quality_status_display().upper(), colors.get(obj.quality_status, "#6b7280"))


# --- Invoice -----------------------------------------------------------------

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = [
        "invoice_number",
        "po_link",
        "formatted_amount",
        "match_badge",
        "invoice_date",
        "due_date",
    ]
    list_filter = ["match_status"]
    search_fields = ["invoice_number", "po__po_number", "vendor_invoice_ref"]
    date_hierarchy = "invoice_date"

    @admin.display(description="PO")
    def po_link(self, obj):
        return obj.po.po_number

    @admin.display(description="Amount")
    def formatted_amount(self, obj):
        return _money(obj.invoice_amount)

    @admin.display(description="Match status")
    def match_badge(self, obj):
        colors = {
            "pending_match": "#d97706",
            "matched": "#16a34a",
            "discrepancy": "#dc2626",
            "approved_for_payment": "#2563eb",
            "paid": "#0d9488",
        }
        return _badge(obj.get_match_status_display().upper(), colors.get(obj.match_status, "#6b7280"))


# --- Approval Request (dashboard) --------------------------------------------

@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    list_display = [
        "request_id_short",
        "tier_badge",
        "entity_type",
        "entity_id",
        "decision_badge",
        "assigned_to",
        "risk_preview",
        "created_at",
    ]
    list_filter = ["decision", "approver_tier", "entity_type"]
    search_fields = ["request_id", "entity_id", "assigned_to"]
    actions = ["approve_requests", "reject_requests"]
    readonly_fields = ["ai_risk_summary", "created_at", "decided_at"]
    date_hierarchy = "created_at"

    def changelist_view(self, request, extra_context=None):
        pending = ApprovalRequest.objects.filter(decision="pending").count()
        extra_context = extra_context or {}
        extra_context["title"] = f"Approval requests — {pending} pending"
        return super().changelist_view(request, extra_context=extra_context)

    @admin.display(description="Request")
    def request_id_short(self, obj):
        return obj.request_id[:8]

    @admin.display(description="Tier")
    def tier_badge(self, obj):
        colors = {
            "manager": "#2563eb",
            "finance": "#7c3aed",
            "cfo": "#dc2626",
            "sole_source_committee": "#d97706",
        }
        return _badge(obj.get_approver_tier_display().upper(), colors.get(obj.approver_tier, "#6b7280"))

    @admin.display(description="Decision")
    def decision_badge(self, obj):
        return _badge(
            obj.get_decision_display().upper(),
            APPROVAL_DECISION_COLORS.get(obj.decision, "#6b7280"),
        )

    @admin.display(description="Risk summary")
    def risk_preview(self, obj):
        if not obj.ai_risk_summary:
            return "—"
        text = obj.ai_risk_summary
        return (text[:70] + "…") if len(text) > 70 else text

    def _decide(self, request, queryset, decision):
        pending = list(queryset.filter(decision="pending"))
        affected_pos = set()
        for req in pending:
            req.decision = decision
            req.decided_at = timezone.now()
            req.decision_reason = f"Bulk {decision} by {request.user.get_username()}"
            req.save(update_fields=["decision", "decided_at", "decision_reason"])
            if req.entity_type == "purchase_order":
                affected_pos.add(req.entity_id)

        transitioned = self._cascade_to_purchase_orders(affected_pos, decision)
        msg = f"{decision.title()} {len(pending)} request(s)."
        if transitioned:
            msg += f" Transitioned {transitioned} purchase order(s)."
        self.message_user(request, msg, messages.SUCCESS)

    def _cascade_to_purchase_orders(self, po_numbers, decision):
        """Reflect approval decisions onto the underlying purchase orders.

        A PO is approved once no approval requests for it remain pending; a
        single rejection rejects the PO.
        """
        transitioned = 0
        for po_number in po_numbers:
            po = PurchaseOrder.objects.filter(po_number=po_number).first()
            if po is None or po.status != PurchaseOrder.Status.PENDING_APPROVAL:
                continue
            reqs = ApprovalRequest.objects.filter(
                entity_type="purchase_order", entity_id=po_number
            )
            try:
                if reqs.filter(decision="rejected").exists():
                    po.reject()
                elif not reqs.filter(decision="pending").exists():
                    po.approve()
                else:
                    continue
            except TransitionNotAllowed:
                continue
            po.save()
            transitioned += 1
        return transitioned

    @admin.action(description="Approve selected requests")
    def approve_requests(self, request, queryset):
        self._decide(request, queryset, "approved")

    @admin.action(description="Reject selected requests")
    def reject_requests(self, request, queryset):
        self._decide(request, queryset, "rejected")


# --- Policy Document ---------------------------------------------------------

@admin.register(PolicyDocument)
class PolicyDocumentAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "policy_type",
        "content_preview",
        "embedding_status",
        "version",
        "effective_date",
    ]
    list_filter = ["policy_type", "version"]
    search_fields = ["title", "content"]
    actions = ["reembed_policies"]
    date_hierarchy = "effective_date"

    @admin.display(description="Preview")
    def content_preview(self, obj):
        text = obj.content
        return (text[:90] + "…") if len(text) > 90 else text

    @admin.display(description="Embedding")
    def embedding_status(self, obj):
        if obj.embedding is not None:
            return _badge("EMBEDDED", "#16a34a")
        return _badge("MISSING", "#d97706")

    @admin.action(description="Re-embed selected policies")
    def reembed_policies(self, request, queryset):
        # The embedding pipeline (Celery + Vertex AI) is wired in a later phase.
        # Clearing the vector marks the document for the backfill command / signal.
        try:
            from .tasks import embed_policy_document_task

            for policy in queryset:
                embed_policy_document_task.delay(policy.id)
            self.message_user(
                request,
                f"Queued {queryset.count()} policy document(s) for re-embedding.",
                messages.SUCCESS,
            )
        except Exception:  # noqa: BLE001 - pipeline not yet available
            queryset.update(embedding=None)
            self.message_user(
                request,
                f"Marked {queryset.count()} policy document(s) for embedding backfill.",
                messages.WARNING,
            )


# --- Audit Ledger (read-only) ------------------------------------------------

@admin.register(AuditLedger)
class AuditLedgerAdmin(admin.ModelAdmin):
    list_display = ["created_at", "action", "entity_type", "entity_id", "actor"]
    list_filter = ["entity_type", "action", "actor"]
    search_fields = ["entity_id", "action", "actor"]
    readonly_fields = [
        "event_id",
        "entity_type",
        "entity_id",
        "action",
        "actor",
        "context_viewer",
        "policy_citations",
        "created_at",
    ]
    exclude = ["context_json"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Context")
    def context_viewer(self, obj):
        pretty = json.dumps(obj.context_json, indent=2, default=str)
        return format_html(
            '<pre style="background:#0f172a;color:#e2e8f0;padding:10px;'
            'border-radius:6px;max-height:320px;overflow:auto;">{}</pre>',
            pretty,
        )


@admin.register(AgentSession)
class AgentSessionAdmin(admin.ModelAdmin):
    list_display = ("session_id", "user", "status_badge", "last_activity")
    list_filter = ("status",)
    search_fields = ("session_id", "user__username", "last_message")
    readonly_fields = (
        "session_id",
        "user",
        "status",
        "last_message",
        "created_at",
        "last_activity",
    )
    date_hierarchy = "last_activity"

    def has_add_permission(self, request):
        return False

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            AgentSession.Status.IDLE: "#64748b",
            AgentSession.Status.RUNNING: "#2563eb",
            AgentSession.Status.AWAITING_APPROVAL: "#d97706",
            AgentSession.Status.FAILED: "#dc2626",
        }
        color = colors.get(obj.status, "#64748b")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:9999px;font-size:11px;">{}</span>',
            color,
            obj.get_status_display(),
        )
