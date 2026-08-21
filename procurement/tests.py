"""Model and API tests for the procurement app."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    AgentSession,
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


class AgentGraphBuildTests(TestCase):
    """The orchestrator graph must compile without contacting any model."""

    def test_graph_compiles_with_expected_nodes(self):
        from agent.graph import build_graph

        graph = build_graph()
        nodes = set(graph.get_graph().nodes.keys())
        for expected in ("retrieve_policy_context", "reason", "act", "hitl_check"):
            self.assertIn(expected, nodes)

    def test_local_tools_expose_ten_tools(self):
        from agent.mcp_client import get_local_tools

        tool_names = {t.name for t in get_local_tools()}
        self.assertEqual(len(tool_names), 10)
        self.assertIn("create_purchase_order", tool_names)


class MCPToolTests(TestCase):
    """Cover the MCP tool primitives that do not require the embedding API."""

    def setUp(self):
        self.vendor = make_vendor()
        self.material = make_material()

    def test_query_material_master(self):
        from mcp_server import tools

        res = tools.query_material_master(self.material.material_number)
        self.assertEqual(res["material_number"], self.material.material_number)
        self.assertIn("reorder_needed", res)

    def test_query_material_master_missing(self):
        from mcp_server import tools

        res = tools.query_material_master("NOPE-000")
        self.assertIn("error", res)

    def test_search_vendors_filters_by_group(self):
        from mcp_server import tools

        res = tools.search_vendors(material_group="raw_materials", min_score=0.0)
        self.assertGreaterEqual(res["count"], 1)
        self.assertEqual(res["vendors"][0]["vendor_code"], self.vendor.vendor_code)

    def test_evaluate_vendor_scorecard_and_flags(self):
        from mcp_server import tools

        res = tools.evaluate_vendor(self.vendor.vendor_code)
        self.assertIn("scorecard", res)
        self.assertIn("recommendation", res)

    def test_create_requisition_and_route(self):
        from mcp_server import tools

        pr = tools.create_purchase_requisition(
            requester="agent@procuremcp.example",
            cost_center="CC-1500",
            justification="Restock",
            line_items=[{"material_number": self.material.material_number, "quantity": 10}],
        )
        self.assertEqual(pr["status"], "draft")
        self.assertIn("pr_number", pr)

        routed = tools.route_for_approval(
            "purchase_requisition", pr["pr_number"], 60000, is_sole_source=True
        )
        self.assertTrue(routed["hitl_pending"])
        tiers = {a["approver_tier"] for a in routed["approval_requests"]}
        self.assertIn("cfo", tiers)
        self.assertIn("sole_source_committee", tiers)

    def test_check_po_status_timeline(self):
        from mcp_server import tools

        po = make_po(self.vendor, total_value=Decimal("5000"))
        res = tools.check_po_status(po.po_number)
        self.assertEqual(res["po_number"], po.po_number)
        self.assertIsInstance(res["timeline"], list)


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


class ApplyDecisionTests(TestCase):
    """The HITL decision must persist to the approval record and the PO FSM."""

    def _pending_po(self, **kwargs):
        vendor = make_vendor()
        po = make_po(vendor, status=PurchaseOrder.Status.DRAFT, **kwargs)
        po.submit_for_approval()
        po.save()
        return po

    def test_approve_transitions_po_and_marks_request(self):
        from .approval_engine import apply_decision

        po = self._pending_po(total_value=Decimal("75000"))
        result = apply_decision("purchase_order", po.po_number, "approved", "ok")

        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.APPROVED)
        self.assertIsNotNone(po.approved_at)
        self.assertTrue(result["transitioned"])
        self.assertEqual(result["approvals_updated"], 1)

        req = ApprovalRequest.objects.get(
            entity_type="purchase_order", entity_id=po.po_number
        )
        self.assertEqual(req.decision, ApprovalRequest.Decision.APPROVED)
        self.assertEqual(req.decision_reason, "ok")
        self.assertIsNotNone(req.decided_at)

    def test_reject_transitions_po_to_rejected(self):
        from .approval_engine import apply_decision

        po = self._pending_po(total_value=Decimal("75000"))
        result = apply_decision("purchase_order", po.po_number, "rejected", "over budget")

        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.REJECTED)
        self.assertTrue(result["transitioned"])
        req = ApprovalRequest.objects.get(entity_id=po.po_number)
        self.assertEqual(req.decision, ApprovalRequest.Decision.REJECTED)

    def test_approve_resolves_both_sole_source_requests(self):
        from .approval_engine import apply_decision

        po = self._pending_po(total_value=Decimal("60000"), is_sole_source=True)
        # Two pending requests (CFO + committee) before the decision.
        self.assertEqual(
            ApprovalRequest.objects.filter(
                entity_id=po.po_number, decision=ApprovalRequest.Decision.PENDING
            ).count(),
            2,
        )
        result = apply_decision("purchase_order", po.po_number, "approved")
        self.assertEqual(result["approvals_updated"], 2)
        self.assertFalse(
            ApprovalRequest.objects.filter(
                entity_id=po.po_number, decision=ApprovalRequest.Decision.PENDING
            ).exists()
        )
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.APPROVED)

    def test_unsupported_decision_returns_error(self):
        from .approval_engine import apply_decision

        po = self._pending_po(total_value=Decimal("75000"))
        result = apply_decision("purchase_order", po.po_number, "maybe")
        self.assertIn("error", result)
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.PENDING_APPROVAL)


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


class AgentSessionModelTests(TestCase):
    def test_create_and_default_status(self):
        s = AgentSession.objects.create(session_id="abc-123")
        self.assertEqual(s.status, AgentSession.Status.IDLE)
        self.assertIsNotNone(s.last_activity)

    def test_status_transitions_and_ordering(self):
        AgentSession.objects.create(session_id="s-old", status=AgentSession.Status.IDLE)
        newer = AgentSession.objects.create(
            session_id="s-new", status=AgentSession.Status.AWAITING_APPROVAL
        )
        ordering = list(AgentSession.objects.values_list("session_id", flat=True))
        self.assertEqual(ordering[0], newer.session_id)


class AgentEndpointAuthTests(TestCase):
    """The agent HTTP endpoints must reject unauthenticated requests."""

    def test_chat_requires_auth(self):
        resp = self.client.post(
            "/api/agent/chat/", data="{}", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 401)

    def test_approve_requires_auth(self):
        resp = self.client.post(
            "/api/agent/approve/", data="{}", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 401)

    def test_sessions_requires_auth(self):
        resp = self.client.get("/api/agent/sessions/")
        self.assertEqual(resp.status_code, 401)

    def test_chat_page_requires_auth_and_redirects(self):
        resp = self.client.get("/chat/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login/", resp["Location"])

    def test_authenticated_sessions_endpoint_returns_only_owner_rows(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        alice = User.objects.create_user("alice", password="pw")
        bob = User.objects.create_user("bob", password="pw")
        AgentSession.objects.create(session_id="alice-1", user=alice)
        AgentSession.objects.create(session_id="bob-1", user=bob)
        AgentSession.objects.create(session_id="anon-1")

        self.client.force_login(alice)
        resp = self.client.get("/api/agent/sessions/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["sessions"][0]["session_id"], "alice-1")


class NotificationWebhookTests(TestCase):
    def _make_approval(self):
        return ApprovalRequest.objects.create(
            entity_type="purchase_order",
            entity_id="PO-TEST-001",
            approver_tier=ApprovalRequest.ApproverTier.CFO,
            assigned_to="cfo@procuremcp.example",
        )

    def test_logs_only_when_webhook_unset(self):
        from unittest.mock import patch

        from django.test import override_settings

        approval = self._make_approval()
        with override_settings(NOTIFICATION_WEBHOOK_URL=""):
            with patch("procurement.tasks._post_webhook") as mock_post:
                from procurement.tasks import send_approval_notification_task

                result = send_approval_notification_task(approval.id)
        mock_post.assert_not_called()
        self.assertEqual(result["status"], "notified")
        self.assertEqual(result["channel"], "log")

    def test_posts_to_webhook_when_configured(self):
        from unittest.mock import patch

        from django.test import override_settings

        approval = self._make_approval()
        with override_settings(NOTIFICATION_WEBHOOK_URL="https://hooks.example.test/x"):
            with patch("procurement.tasks._post_webhook", return_value=200) as mock_post:
                from procurement.tasks import send_approval_notification_task

                result = send_approval_notification_task(approval.id)
        mock_post.assert_called_once()
        url_arg, payload_arg = mock_post.call_args.args[0], mock_post.call_args.args[1]
        self.assertEqual(url_arg, "https://hooks.example.test/x")
        self.assertEqual(payload_arg["event"], "approval.pending")
        self.assertEqual(payload_arg["tier"], ApprovalRequest.ApproverTier.CFO)
        self.assertEqual(payload_arg["entity_id"], "PO-TEST-001")
        self.assertEqual(result["channel"], "webhook")
        self.assertEqual(result["http_status"], 200)

    def test_webhook_failure_is_captured_not_raised(self):
        from unittest.mock import patch
        from urllib.error import URLError

        from django.test import override_settings

        approval = self._make_approval()
        with override_settings(NOTIFICATION_WEBHOOK_URL="https://hooks.example.test/x"):
            with patch("procurement.tasks._post_webhook", side_effect=URLError("boom")):
                from procurement.tasks import send_approval_notification_task

                result = send_approval_notification_task(approval.id)
        self.assertEqual(result["status"], "webhook_failed")
        self.assertIn("boom", result["error"])
