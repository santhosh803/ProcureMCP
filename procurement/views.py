"""DRF viewsets for the procurement API plus purchase-order lifecycle actions."""

from django_fsm import TransitionNotAllowed
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    ApprovalRequest,
    AuditLedger,
    GoodsReceipt,
    Invoice,
    Material,
    PolicyDocument,
    PurchaseOrder,
    PurchaseRequisition,
    VendorMaster,
)
from .serializers import (
    ApprovalRequestSerializer,
    AuditLedgerSerializer,
    GoodsReceiptSerializer,
    InvoiceSerializer,
    MaterialSerializer,
    PolicyDocumentSerializer,
    PurchaseOrderSerializer,
    PurchaseOrderWriteSerializer,
    PurchaseRequisitionSerializer,
    VendorMasterSerializer,
)


class MaterialViewSet(viewsets.ModelViewSet):
    queryset = Material.objects.all()
    serializer_class = MaterialSerializer
    filterset_fields = ["material_group", "unit_of_measure"]
    search_fields = ["material_number", "description"]
    ordering_fields = ["material_number", "base_price", "stock_qty"]


class VendorMasterViewSet(viewsets.ModelViewSet):
    queryset = VendorMaster.objects.all()
    serializer_class = VendorMasterSerializer
    filterset_fields = ["status", "country", "payment_terms"]
    search_fields = ["vendor_code", "name"]
    ordering_fields = ["vendor_code", "overall_score", "on_time_delivery_pct"]


class PurchaseRequisitionViewSet(viewsets.ModelViewSet):
    queryset = PurchaseRequisition.objects.prefetch_related("line_items").all()
    serializer_class = PurchaseRequisitionSerializer
    filterset_fields = ["status", "cost_center", "requester"]
    search_fields = ["pr_number", "requester", "justification"]
    ordering_fields = ["created_at", "total_value"]


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    queryset = (
        PurchaseOrder.objects.select_related("vendor", "source_pr")
        .prefetch_related("line_items", "goods_receipts", "invoices")
        .all()
    )
    filterset_fields = ["status", "is_sole_source", "vendor", "currency"]
    search_fields = ["po_number", "vendor__name", "vendor__vendor_code"]
    ordering_fields = ["created_at", "total_value"]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return PurchaseOrderWriteSerializer
        return PurchaseOrderSerializer

    def _run_transition(self, request, transition_name, *, required_perm=None):
        """Invoke a django-fsm transition and persist, returning a DRF Response."""
        po = self.get_object()
        if required_perm and not request.user.has_perm(required_perm):
            return Response(
                {"detail": "You do not have permission for this transition."},
                status=status.HTTP_403_FORBIDDEN,
            )
        method = getattr(po, transition_name)
        try:
            result = method()
        except TransitionNotAllowed:
            return Response(
                {"detail": f"Cannot {transition_name} a PO in '{po.status}' state."},
                status=status.HTTP_409_CONFLICT,
            )
        po.save()
        data = PurchaseOrderSerializer(po).data
        if transition_name == "submit_for_approval" and result:
            data["approvals_created"] = [
                {"tier": a.approver_tier, "assigned_to": a.assigned_to} for a in result
            ]
        return Response(data)

    @action(detail=True, methods=["post"])
    def submit_for_approval(self, request, pk=None):
        return self._run_transition(request, "submit_for_approval")

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._run_transition(
            request, "approve", required_perm="procurement.change_purchaseorder"
        )

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._run_transition(
            request, "reject", required_perm="procurement.change_purchaseorder"
        )

    @action(detail=True, methods=["post"])
    def send_to_vendor(self, request, pk=None):
        return self._run_transition(request, "send_to_vendor")

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._run_transition(request, "cancel")


class GoodsReceiptViewSet(viewsets.ModelViewSet):
    queryset = GoodsReceipt.objects.select_related("po").prefetch_related("line_items").all()
    serializer_class = GoodsReceiptSerializer
    filterset_fields = ["quality_status", "po"]
    search_fields = ["gr_number", "po__po_number", "received_by"]
    ordering_fields = ["received_at"]


class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.select_related("po").all()
    serializer_class = InvoiceSerializer
    filterset_fields = ["match_status", "po"]
    search_fields = ["invoice_number", "vendor_invoice_ref", "po__po_number"]
    ordering_fields = ["invoice_date", "due_date", "invoice_amount"]


class ApprovalRequestViewSet(viewsets.ModelViewSet):
    queryset = ApprovalRequest.objects.all()
    serializer_class = ApprovalRequestSerializer
    filterset_fields = ["decision", "approver_tier", "entity_type", "assigned_to"]
    search_fields = ["request_id", "entity_id", "assigned_to"]
    ordering_fields = ["created_at", "decided_at"]


class PolicyDocumentViewSet(viewsets.ModelViewSet):
    queryset = PolicyDocument.objects.all()
    serializer_class = PolicyDocumentSerializer
    filterset_fields = ["policy_type", "version"]
    search_fields = ["title", "content"]
    ordering_fields = ["effective_date", "title"]


class AuditLedgerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLedger.objects.all()
    serializer_class = AuditLedgerSerializer
    filterset_fields = ["entity_type", "action", "actor"]
    search_fields = ["entity_id", "action", "actor"]
    ordering_fields = ["created_at"]
