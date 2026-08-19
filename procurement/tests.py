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
        from django.contrib.auth import get_user_model

        self.client = APIClient()
        self.vendor = make_vendor()
        self.approver = get_user_model().objects.create_superuser(
            "approver", "approver@procuremcp.example", "pw"
        )

    def test_list_purchase_orders(self):
        make_po(self.vendor)
        resp = self.client.get("/api/purchase-orders/")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.data["count"], 1)

    def test_approve_action_requires_pending_state(self):
        self.client.force_authenticate(self.approver)
        po = make_po(self.vendor, status=PurchaseOrder.Status.DRAFT)
        resp = self.client.post(f"/api/purchase-orders/{po.id}/approve/")
        self.assertEqual(resp.status_code, 409)

    def test_approve_requires_permission(self):
        po = make_po(self.vendor, status=PurchaseOrder.Status.PENDING_APPROVAL)
        resp = self.client.post(f"/api/purchase-orders/{po.id}/approve/")
        self.assertEqual(resp.status_code, 403)

    def test_approve_transitions_pending_to_approved(self):
        self.client.force_authenticate(self.approver)
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


class PolicyRetrieverTests(TestCase):
    """Exercise the pgvector cosine-similarity ordering without hitting Vertex."""

    def _vec(self, *leading):
        v = list(leading)
        return v + [0.0] * (768 - len(v))

    def test_cosine_ranking_orders_by_similarity(self):
        from unittest.mock import patch

        near = PolicyDocument.objects.create(
            title="Sole-Source Justification",
            policy_type=PolicyDocument.PolicyType.SOLE_SOURCE,
            content="Single-source purchases require written justification.",
            effective_date=date.today(),
        )
        near.embedding = self._vec(1.0, 0.0, 0.0)
        near.save(update_fields=["embedding"])

        far = PolicyDocument.objects.create(
            title="Payment Terms Standard",
            policy_type=PolicyDocument.PolicyType.CATEGORY_RULE,
            content="Default payment term is NET30.",
            effective_date=date.today(),
        )
        far.embedding = self._vec(0.0, 1.0, 0.0)
        far.save(update_fields=["embedding"])

        from agent import retriever

        with patch.object(retriever, "embed_text", return_value=self._vec(1.0, 0.0, 0.0)):
            results = retriever.retrieve_policy_context("sole source rules", k=2)

        self.assertEqual(results[0]["title"], "Sole-Source Justification")
        self.assertGreater(results[0]["similarity_score"], results[1]["similarity_score"])

    def test_policy_type_filter(self):
        from unittest.mock import patch

        p = PolicyDocument.objects.create(
            title="CFO Approval Threshold",
            policy_type=PolicyDocument.PolicyType.SPENDING_LIMIT,
            content="POs of USD 50,000+ require CFO approval.",
            effective_date=date.today(),
        )
        p.embedding = self._vec(1.0)
        p.save(update_fields=["embedding"])

        from agent import retriever

        with patch.object(retriever, "embed_text", return_value=self._vec(1.0)):
            results = retriever.retrieve_policy_context(
                "approval", k=5, policy_types=["sole_source"]
            )
        self.assertEqual(results, [])


class ApprovalRoutingTests(TestCase):
    def test_tier_thresholds(self):
        from .approval_engine import tier_for_value

        self.assertEqual(tier_for_value(Decimal("9999.99")), ApprovalRequest.ApproverTier.MANAGER)
        # Exactly at the manager ceiling routes to finance.
        self.assertEqual(tier_for_value(Decimal("10000")), ApprovalRequest.ApproverTier.FINANCE)
        self.assertEqual(tier_for_value(Decimal("49999.99")), ApprovalRequest.ApproverTier.FINANCE)
        # Exactly at the finance ceiling routes to CFO.
        self.assertEqual(tier_for_value(Decimal("50000")), ApprovalRequest.ApproverTier.CFO)
        self.assertEqual(tier_for_value(Decimal("75000")), ApprovalRequest.ApproverTier.CFO)

    def test_route_approval_creates_cfo_request_for_high_value(self):
        from .approval_engine import route_approval

        vendor = make_vendor()
        po = make_po(vendor, total_value=Decimal("75000"))
        created = route_approval(po, po.total_value, po.is_sole_source)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].approver_tier, ApprovalRequest.ApproverTier.CFO)

    def test_sole_source_creates_parallel_committee_request(self):
        from .approval_engine import route_approval

        vendor = make_vendor()
        po = make_po(vendor, total_value=Decimal("12000"), is_sole_source=True)
        created = route_approval(po, po.total_value, is_sole_source=True)
        tiers = {a.approver_tier for a in created}
        self.assertIn(ApprovalRequest.ApproverTier.FINANCE, tiers)
        self.assertIn(ApprovalRequest.ApproverTier.SOLE_SOURCE_COMMITTEE, tiers)


class StateMachineTests(TestCase):
    def setUp(self):
        self.vendor = make_vendor()

    def test_submit_moves_to_pending_and_routes_cfo(self):
        po = make_po(self.vendor, total_value=Decimal("60000"))
        po.submit_for_approval()
        po.save()
        self.assertEqual(po.status, PurchaseOrder.Status.PENDING_APPROVAL)
        approval = ApprovalRequest.objects.get(
            entity_type="purchase_order", entity_id=po.po_number
        )
        self.assertEqual(approval.approver_tier, ApprovalRequest.ApproverTier.CFO)

    def test_invalid_transition_raises(self):
        from django_fsm import TransitionNotAllowed

        po = make_po(self.vendor, status=PurchaseOrder.Status.DRAFT)
        with self.assertRaises(TransitionNotAllowed):
            po.approve()  # cannot approve directly from draft

    def test_full_lifecycle(self):
        po = make_po(self.vendor, total_value=Decimal("5000"))
        po.submit_for_approval(); po.save()
        po.approve(); po.save()
        self.assertIsNotNone(po.approved_at)
        po.send_to_vendor(); po.save()
        self.assertIsNotNone(po.sent_at)

        material = make_material()
        POLineItem.objects.create(
            po=po, material=material,
            quantity_ordered=Decimal("10"), quantity_received=Decimal("10"),
            unit_price=Decimal("2.50"), delivery_date=date.today(),
        )
        po.record_receipt(); po.save()
        self.assertEqual(po.status, PurchaseOrder.Status.FULLY_RECEIVED)
        po.mark_invoiced(); po.save()
        po.close(); po.save()
        self.assertEqual(po.status, PurchaseOrder.Status.CLOSED)

    def test_partial_then_full_receipt(self):
        po = make_po(self.vendor, status=PurchaseOrder.Status.SENT_TO_VENDOR)
        material = make_material()
        line = POLineItem.objects.create(
            po=po, material=material,
            quantity_ordered=Decimal("10"), quantity_received=Decimal("4"),
            unit_price=Decimal("2.50"), delivery_date=date.today(),
        )
        po.record_receipt(); po.save()
        self.assertEqual(po.status, PurchaseOrder.Status.PARTIALLY_RECEIVED)
        line.quantity_received = Decimal("10")
        line.save()
        po.record_receipt(); po.save()
        self.assertEqual(po.status, PurchaseOrder.Status.FULLY_RECEIVED)

    def test_reject_transition(self):
        po = make_po(self.vendor, total_value=Decimal("5000"))
        po.submit_for_approval(); po.save()
        po.reject(); po.save()
        self.assertEqual(po.status, PurchaseOrder.Status.REJECTED)
