"""DRF serializers for the procurement domain.

PurchaseOrder is exposed as a rich, nested read serializer (line items, vendor
summary, approvals, goods receipts, invoices) with a separate lightweight write
serializer. Other entities use straightforward model serializers.
"""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import (
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


class MaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Material
        fields = "__all__"


class VendorMasterSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorMaster
        fields = "__all__"


class VendorSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorMaster
        fields = ["vendor_code", "name", "status", "overall_score", "payment_terms"]


class PolicyDocumentSerializer(serializers.ModelSerializer):
    has_embedding = serializers.SerializerMethodField()

    class Meta:
        model = PolicyDocument
        exclude = ["embedding"]

    def get_has_embedding(self, obj) -> bool:
        return obj.embedding is not None


# --- Requisitions ------------------------------------------------------------

class PRLineItemSerializer(serializers.ModelSerializer):
    material_number = serializers.CharField(source="material.material_number", read_only=True)
    material_description = serializers.CharField(source="material.description", read_only=True)

    class Meta:
        model = PRLineItem
        fields = [
            "id",
            "material",
            "material_number",
            "material_description",
            "quantity",
            "estimated_unit_price",
            "delivery_date_needed",
        ]


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    line_items = PRLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseRequisition
        fields = "__all__"
        read_only_fields = ["pr_number", "created_at"]


# --- Purchase Orders ---------------------------------------------------------

class POLineItemSerializer(serializers.ModelSerializer):
    material_number = serializers.CharField(source="material.material_number", read_only=True)
    material_description = serializers.CharField(source="material.description", read_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = POLineItem
        fields = [
            "id",
            "material",
            "material_number",
            "material_description",
            "quantity_ordered",
            "quantity_received",
            "unit_price",
            "line_total",
            "delivery_date",
        ]

    def get_line_total(self, obj) -> str:
        return str(obj.quantity_ordered * obj.unit_price)


class GRLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = GRLineItem
        fields = ["id", "po_line", "quantity_received"]


class GoodsReceiptSerializer(serializers.ModelSerializer):
    line_items = GRLineItemSerializer(many=True, read_only=True)
    po_number = serializers.CharField(source="po.po_number", read_only=True)

    class Meta:
        model = GoodsReceipt
        fields = "__all__"
        read_only_fields = ["gr_number", "received_at"]


class InvoiceSerializer(serializers.ModelSerializer):
    po_number = serializers.CharField(source="po.po_number", read_only=True)

    class Meta:
        model = Invoice
        fields = "__all__"


class ApprovalRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalRequest
        fields = "__all__"
        read_only_fields = ["request_id", "created_at"]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    """Rich, nested read representation of a purchase order."""

    line_items = POLineItemSerializer(many=True, read_only=True)
    vendor_detail = VendorSummarySerializer(source="vendor", read_only=True)
    goods_receipts = GoodsReceiptSerializer(many=True, read_only=True)
    invoices = InvoiceSerializer(many=True, read_only=True)
    approvals = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = "__all__"
        read_only_fields = ["po_number", "created_at", "approved_at", "sent_at"]

    @extend_schema_field(ApprovalRequestSerializer(many=True))
    def get_approvals(self, obj):
        approvals = ApprovalRequest.objects.filter(
            entity_type="purchase_order", entity_id=obj.po_number
        )
        return ApprovalRequestSerializer(approvals, many=True).data


class PurchaseOrderWriteSerializer(serializers.ModelSerializer):
    """Lightweight serializer for creating/updating purchase orders."""

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "po_number",
            "source_pr",
            "vendor",
            "total_value",
            "currency",
            "status",
            "is_sole_source",
            "sole_source_justification",
            "policy_compliance_notes",
        ]
        read_only_fields = ["po_number"]


class AuditLedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLedger
        fields = "__all__"
