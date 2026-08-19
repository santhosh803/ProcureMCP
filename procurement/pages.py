"""Template views for the minimal operator frontend (landing + agent chat)."""

from django.shortcuts import render

from .models import (
    ApprovalRequest,
    PolicyDocument,
    PurchaseOrder,
    VendorMaster,
)


def admin_home(request):
    """Landing page with operational tiles and live counts."""
    context = {
        "po_open_count": PurchaseOrder.objects.exclude(
            status__in=[PurchaseOrder.Status.CLOSED, PurchaseOrder.Status.CANCELLED]
        ).count(),
        "pending_approvals": ApprovalRequest.objects.filter(decision="pending").count(),
        "vendor_count": VendorMaster.objects.filter(status="approved").count(),
        "policy_count": PolicyDocument.objects.count(),
    }
    return render(request, "admin_home.html", context)


def chat_page(request):
    """Agent chat interface."""
    return render(request, "chat.html")
