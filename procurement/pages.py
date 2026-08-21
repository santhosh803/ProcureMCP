"""Template views for the minimal operator frontend (landing + agent chat)."""

from django.contrib.auth.decorators import login_required
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


QUICK_PROMPTS = [
    "Walk me through creating a purchase order end to end",
    "What is the approval limit for a $12,000 indirect purchase?",
    "How is a vendor's overall performance score calculated?",
    "When does a purchase require sole-source committee review?",
    "Route a $60,000 sole-source purchase for approval",
    "Show me policies for capital equipment purchases",
    "How does invoice-to-PO three-way matching work?",
    "Which vendors have the best on-time delivery for raw materials?",
]


@login_required
def chat_page(request):
    """Agent chat interface. Requires an authenticated Django session."""
    return render(request, "chat.html", {"quick_prompts": QUICK_PROMPTS})
