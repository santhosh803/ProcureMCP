"""Procurement API routes (DRF DefaultRouter)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import agent_views, views

router = DefaultRouter()
router.register(r"materials", views.MaterialViewSet)
router.register(r"vendors", views.VendorMasterViewSet)
router.register(r"requisitions", views.PurchaseRequisitionViewSet)
router.register(r"purchase-orders", views.PurchaseOrderViewSet)
router.register(r"goods-receipts", views.GoodsReceiptViewSet)
router.register(r"invoices", views.InvoiceViewSet)
router.register(r"approvals", views.ApprovalRequestViewSet)
router.register(r"policies", views.PolicyDocumentViewSet)
router.register(r"audit", views.AuditLedgerViewSet)

urlpatterns = [
    path("", include(router.urls)),
    path("agent/chat/", agent_views.agent_chat, name="agent-chat"),
    path("agent/approve/", agent_views.agent_approve, name="agent-approve"),
    path("agent/sessions/", agent_views.agent_sessions, name="agent-sessions"),
]

