"""Model and API tests for the procurement app."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    ApprovalRequest,
    AuditLedger,
    GoodsReceipt,
    Invoice,
    Material,
    POLineItem,
    PolicyDocument,
    PurchaseOrder,
    PurchaseRequisition,
    VendorMaster,
)


def make_vendor(**kwargs):
    defaults = dict(
        vendor_code="VEN-90001",
        name="Acme Industrial Supplies",
        country="United States",
        material_groups=["raw_materials"],
        status=VendorMaster.Status.APPROVED,
        on_time_delivery_pct=95.0,
        quality_rating=4.5,
        price_competitiveness=4.0,
        overall_score=4.3,
    )
    defaults.update(kwargs)
    return VendorMaster.objects.create(**defaults)


def make_material(**kwargs):
    defaults = dict(
        material_number="MAT-STL-BLT-001",
        description="Steel Bolt M8x40",
        material_group="raw_materials",
        unit_of_measure="EA",
        base_price=Decimal("2.50"),
    )
    defaults.update(kwargs)
    return Material.objects.create(**defaults)


def make_po(vendor, **kwargs):
    defaults = dict(
        vendor=vendor,
        total_value=Decimal("5000.00"),
        status=PurchaseOrder.Status.DRAFT,
    )
    defaults.update(kwargs)
    return PurchaseOrder.objects.create(**defaults)


class AuditLedgerSignalTests(TestCase):
    def test_purchase_order_creation_writes_audit_entry(self):
        vendor = make_vendor()
        po = make_po(vendor)
        entries = AuditLedger.objects.filter(
            entity_type="purchase_order", entity_id=po.po_number
        )
        self.assertTrue(entries.exists())
        self.assertEqual(entries.first().action, "created")

    def test_status_update_writes_second_audit_entry(self):
        vendor = make_vendor()
        po = make_po(vendor)
        po.status = PurchaseOrder.Status.PENDING_APPROVAL
        po.save()
        entries = AuditLedger.objects.filter(
            entity_type="purchase_order", entity_id=po.po_number
        )
        self.assertGreaterEqual(entries.count(), 2)
        self.assertTrue(
            entries.filter(action="updated:pending_approval").exists()
        )

    def test_requisition_and_approval_are_audited(self):
        pr = PurchaseRequisition.objects.create(
            requester="buyer@procuremcp.example",
            cost_center="CC-1001",
            justification="Restock fasteners",
            total_value=Decimal("1200.00"),
        )
        ApprovalRequest.objects.create(
            entity_type="purchase_requisition",
            entity_id=pr.pr_number,
            approver_tier=ApprovalRequest.ApproverTier.MANAGER,
        )
        self.assertTrue(
            AuditLedger.objects.filter(
                entity_type="purchase_requisition", entity_id=pr.pr_number
            ).exists()
        )
        self.assertTrue(
            AuditLedger.objects.filter(entity_type="approval_request").exists()
        )


class PolicyDocumentTests(TestCase):
    def test_policy_creation_audited_via_no_failure(self):
        # Embedding pipeline is absent in Phase 2; creation must still succeed.
        policy = PolicyDocument.objects.create(
            title="Manager Approval Threshold",
            policy_type=PolicyDocument.PolicyType.SPENDING_LIMIT,
            content="POs below USD 10,000 require manager approval.",
            effective_date=date.today(),
        )
        self.assertIsNone(policy.embedding)


class PurchaseOrderAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.vendor = make_vendor()

    def test_list_purchase_orders(self):
        make_po(self.vendor)
        resp = self.client.get("/api/purchase-orders/")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.data["count"], 1)

    def test_approve_action_requires_pending_state(self):
        po = make_po(self.vendor, status=PurchaseOrder.Status.DRAFT)
        resp = self.client.post(f"/api/purchase-orders/{po.id}/approve/")
        self.assertEqual(resp.status_code, 409)

    def test_approve_transitions_pending_to_approved(self):
        po = make_po(self.vendor, status=PurchaseOrder.Status.PENDING_APPROVAL)
        resp = self.client.post(f"/api/purchase-orders/{po.id}/approve/")
        self.assertEqual(resp.status_code, 200)
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.APPROVED)
        self.assertIsNotNone(po.approved_at)

    def test_send_to_vendor_and_cancel_flow(self):
        po = make_po(self.vendor, status=PurchaseOrder.Status.APPROVED)
        resp = self.client.post(f"/api/purchase-orders/{po.id}/send_to_vendor/")
        self.assertEqual(resp.status_code, 200)
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.SENT_TO_VENDOR)

        cancel = self.client.post(f"/api/purchase-orders/{po.id}/cancel/")
        self.assertEqual(cancel.status_code, 200)
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.CANCELLED)

    def test_nested_serializer_includes_line_items_and_vendor(self):
        po = make_po(self.vendor)
        material = make_material()
        POLineItem.objects.create(
            po=po,
            material=material,
            quantity_ordered=Decimal("10"),
            unit_price=Decimal("2.50"),
            delivery_date=date.today() + timedelta(days=7),
        )
        resp = self.client.get(f"/api/purchase-orders/{po.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["line_items"]), 1)
        self.assertEqual(resp.data["vendor_detail"]["vendor_code"], self.vendor.vendor_code)


class AuditAPITests(TestCase):
    def test_audit_endpoint_is_read_only(self):
        client = APIClient()
        resp = client.post("/api/audit/", {"action": "hack"}, format="json")
        self.assertIn(resp.status_code, (403, 405))


class SchemaTests(TestCase):
    def test_openapi_schema_available(self):
        resp = self.client.get("/api/schema/")
        self.assertEqual(resp.status_code, 200)
