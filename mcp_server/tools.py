"""ProcureMCP tool implementations — the enterprise procurement primitives.

Each function performs one atomic Procure-to-Pay operation against the Django
ORM and returns a rich, JSON-serialisable dict. These are framework-agnostic:
the MCP server wraps them, but they can equally be called from a test script or
any other agent runtime. Approval-gated operations return ``hitl_pending`` with a
risk summary rather than silently proceeding.
"""

import functools
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Three-way match tolerance (from the "Three-Way Match Policy").
MATCH_TOLERANCE_PCT = Decimal("0.02")
MATCH_TOLERANCE_ABS = Decimal("100")


def tool_errors(func):
    """Wrap a tool so unexpected errors return a structured payload."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - tools must return, not raise, to the agent
            logger.exception("Tool %s failed", func.__name__)
            return {"error": f"{type(exc).__name__}: {exc}", "tool": func.__name__}

    return wrapper


def _dec(value, default="0"):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _parse_date(value, default=None):
    if not value:
        return default or date.today()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return default or date.today()


# ---------------------------------------------------------------------------
# 1. create_purchase_requisition
# ---------------------------------------------------------------------------

@tool_errors
def create_purchase_requisition(requester, cost_center, justification, line_items):
    """Draft a purchase requisition with line items.

    line_items: list of {material_number, quantity, estimated_unit_price?,
    delivery_date_needed?}. Missing prices default to the material base price and
    missing dates to today + the material lead time.
    """
    from procurement.models import Material, PRLineItem, PurchaseRequisition

    if not line_items:
        return {"error": "At least one line item is required."}

    with transaction.atomic():
        pr = PurchaseRequisition.objects.create(
            requester=requester,
            cost_center=cost_center,
            justification=justification,
            status=PurchaseRequisition.Status.DRAFT,
        )
        total = Decimal("0")
        created_lines = []
        for item in line_items:
            material = Material.objects.filter(
                material_number=item.get("material_number")
            ).first()
            if material is None:
                raise ValueError(f"Unknown material_number: {item.get('material_number')}")
            qty = _dec(item.get("quantity", 1))
            unit = _dec(item.get("estimated_unit_price") or material.base_price)
            needed = _parse_date(
                item.get("delivery_date_needed"),
                date.today() + timedelta(days=material.lead_time_days),
            )
            PRLineItem.objects.create(
                pr=pr,
                material=material,
                quantity=qty,
                estimated_unit_price=unit,
                delivery_date_needed=needed,
            )
            total += qty * unit
            created_lines.append(
                {"material_number": material.material_number, "quantity": str(qty), "unit_price": str(unit)}
            )
        pr.total_value = total
        pr.save(update_fields=["total_value"])

    return {
        "pr_number": pr.pr_number,
        "status": pr.status,
        "requester": pr.requester,
        "cost_center": pr.cost_center,
        "total_value": str(pr.total_value),
        "line_items": created_lines,
        "message": "Purchase requisition created in draft status.",
    }


# ---------------------------------------------------------------------------
# 2. search_vendors
# ---------------------------------------------------------------------------

@tool_errors
def search_vendors(material_group=None, min_score=0.0, region=None, limit=10):
    """Search approved vendors by material group, minimum score, and region."""
    from django.db.models import Q
    from procurement.models import VendorMaster

    qs = VendorMaster.objects.all()
    if material_group:
        norm = str(material_group).strip().lower().replace("-", "_")
        aliases = {norm, norm.replace("_", " "), norm.replace(" ", "_")}
        if "raw" in norm:
            aliases.update(["raw_materials", "raw materials", "raw_material", "raw material"])
        if "capex" in norm or "capital" in norm or "equip" in norm:
            aliases.update(["capex", "capital_equipment", "capital equipment"])
        if "indirect" in norm:
            aliases.update(["indirect", "indirect_materials", "indirect materials"])
        if "service" in norm:
            aliases.update(["services", "service"])

        q_filter = Q()
        for alias in aliases:
            q_filter |= Q(material_groups__contains=[alias])
        qs = qs.filter(q_filter)
    if min_score:
        qs = qs.filter(overall_score__gte=float(min_score))
    if region:
        qs = qs.filter(country__icontains=region)
    qs = qs.order_by("-overall_score")[: int(limit)]

    vendors = [
        {
            "vendor_code": v.vendor_code,
            "name": v.name,
            "country": v.country,
            "status": v.status,
            "overall_score": v.overall_score,
            "on_time_delivery_pct": v.on_time_delivery_pct,
            "quality_rating": v.quality_rating,
            "price_competitiveness": v.price_competitiveness,
            "payment_terms": v.payment_terms,
            "material_groups": v.material_groups,
        }
        for v in qs
    ]
    return {
        "count": len(vendors),
        "filters": {"material_group": material_group, "min_score": min_score, "region": region},
        "vendors": vendors,
    }


# ---------------------------------------------------------------------------
# 3. evaluate_vendor
# ---------------------------------------------------------------------------

@tool_errors
def evaluate_vendor(vendor_code):
    """Return a full vendor scorecard with risk flags and a recommendation."""
    from procurement.models import PurchaseOrder, VendorMaster

    vendor = VendorMaster.objects.filter(vendor_code=vendor_code).first()
    if vendor is None:
        return {"error": f"Vendor not found: {vendor_code}"}

    po_count = PurchaseOrder.objects.filter(vendor=vendor).count()
    risk_flags = []
    if vendor.status == VendorMaster.Status.BLACKLISTED:
        risk_flags.append("Vendor is BLACKLISTED — orders prohibited.")
    if vendor.status == VendorMaster.Status.UNDER_REVIEW:
        risk_flags.append("Vendor is under review — procurement-lead authorisation required.")
    if vendor.overall_score < 3.0:
        risk_flags.append("Overall performance score below the 3.0 minimum for high-value POs.")
    if vendor.on_time_delivery_pct < 80:
        risk_flags.append("On-time delivery below 80%.")

    if vendor.status == VendorMaster.Status.BLACKLISTED:
        recommendation = "Do not order. Vendor is blacklisted."
    elif vendor.overall_score >= 4.0 and vendor.status == VendorMaster.Status.APPROVED:
        recommendation = "Preferred vendor — strong performance across metrics."
    elif vendor.overall_score >= 3.0:
        recommendation = "Acceptable vendor. Suitable for standard purchases."
    else:
        recommendation = "Use with caution — document an exception for values above USD 20,000."

    return {
        "vendor_code": vendor.vendor_code,
        "name": vendor.name,
        "status": vendor.status,
        "scorecard": {
            "on_time_delivery_pct": vendor.on_time_delivery_pct,
            "quality_rating": vendor.quality_rating,
            "price_competitiveness": vendor.price_competitiveness,
            "overall_score": vendor.overall_score,
        },
        "total_orders": vendor.total_orders,
        "actual_po_count": po_count,
        "payment_terms": vendor.payment_terms,
        "material_groups": vendor.material_groups,
        "risk_flags": risk_flags,
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# 9. check_policy_compliance  (defined early — used by create_purchase_order)
# ---------------------------------------------------------------------------

@tool_errors
def check_policy_compliance(query, entity_type=None, entity_value=None):
    """RAG query against the policy corpus; returns cited snippets + assessment."""
    from agent.retriever import retrieve_policy_context

    citations = retrieve_policy_context(query, k=5)

    assessment_parts = []
    required_tier = None
    if entity_value is not None:
        value = _dec(entity_value)
        if value < Decimal("10000"):
            required_tier = "manager"
        elif value < Decimal("50000"):
            required_tier = "finance"
        else:
            required_tier = "cfo"
        assessment_parts.append(
            f"Value USD {value:,.2f} requires {required_tier}-tier approval."
        )
    if not citations:
        assessment_parts.append("No embedded policies matched — ensure the corpus is embedded.")
    else:
        assessment_parts.append(
            f"{len(citations)} relevant policy snippet(s) retrieved for review."
        )

    return {
        "query": query,
        "entity_type": entity_type,
        "required_tier": required_tier,
        "status": "review_required",
        "assessment": " ".join(assessment_parts),
        "citations": citations,
    }


# ---------------------------------------------------------------------------
# 10. route_for_approval
# ---------------------------------------------------------------------------

@tool_errors
def route_for_approval(entity_type, entity_id, value, is_sole_source=False):
    """Create multi-tier approval request(s) for an entity."""
    from procurement.approval_engine import route_approval
    from procurement.models import PurchaseOrder, PurchaseRequisition

    entity = None
    if entity_type == "purchase_order":
        entity = PurchaseOrder.objects.filter(po_number=entity_id).first()
    elif entity_type == "purchase_requisition":
        entity = PurchaseRequisition.objects.filter(pr_number=entity_id).first()

    if entity is not None:
        created = route_approval(entity, value, is_sole_source)
    else:
        # Fall back to a lightweight routing when the entity is external.
        class _Ref:
            pass

        ref = _Ref()
        ref.entity_type = entity_type
        ref.pk = entity_id
        created = route_approval(ref, value, is_sole_source)

    return {
        "hitl_pending": True,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "approval_requests": [
            {
                "request_id": a.request_id,
                "approver_tier": a.approver_tier,
                "assigned_to": a.assigned_to,
                "decision": a.decision,
                "ai_risk_summary": a.ai_risk_summary,
            }
            for a in created
        ],
    }


# ---------------------------------------------------------------------------
# 4. create_purchase_order
# ---------------------------------------------------------------------------

@tool_errors
def create_purchase_order(pr_number, vendor_code, is_sole_source=False, sole_source_justification=""):
    """Convert an approved PR into a PO, run policy RAG, and route for approval."""
    from procurement.models import (
        POLineItem,
        PurchaseOrder,
        PurchaseRequisition,
        VendorMaster,
    )

    pr = PurchaseRequisition.objects.filter(pr_number=pr_number).first()
    if pr is None:
        return {"error": f"Purchase requisition not found: {pr_number}"}
    vendor = VendorMaster.objects.filter(vendor_code=vendor_code).first()
    if vendor is None:
        return {"error": f"Vendor not found: {vendor_code}"}
    if vendor.status == VendorMaster.Status.BLACKLISTED:
        return {"error": f"Vendor {vendor_code} is blacklisted — cannot create PO."}

    pr_lines = list(pr.line_items.select_related("material").all())
    if not pr_lines:
        return {"error": f"Requisition {pr_number} has no line items."}

    # Policy compliance check (RAG) before committing.
    total_value = sum((line.quantity * line.estimated_unit_price for line in pr_lines), Decimal("0"))
    groups = sorted({line.material.material_group for line in pr_lines})
    compliance = check_policy_compliance(
        query=(
            f"Purchase order for {', '.join(groups)} materials valued at "
            f"USD {total_value} from vendor {vendor.name}"
            + (" as a sole-source purchase" if is_sole_source else "")
        ),
        entity_type="purchase_order",
        entity_value=total_value,
    )
    citations = compliance.get("citations", []) if isinstance(compliance, dict) else []

    with transaction.atomic():
        po = PurchaseOrder.objects.create(
            source_pr=pr,
            vendor=vendor,
            total_value=total_value,
            is_sole_source=is_sole_source,
            sole_source_justification=sole_source_justification or "",
            policy_compliance_notes=citations,
            status=PurchaseOrder.Status.DRAFT,
        )
        for line in pr_lines:
            POLineItem.objects.create(
                po=po,
                material=line.material,
                quantity_ordered=line.quantity,
                unit_price=line.estimated_unit_price,
                delivery_date=line.delivery_date_needed,
            )
        # Submit -> pending_approval, which routes the required approvals.
        approvals = po.submit_for_approval()
        po.save()

        pr.status = PurchaseRequisition.Status.CONVERTED_TO_PO
        pr.save(update_fields=["status"])

    risk_summary = approvals[0].ai_risk_summary if approvals else ""
    return {
        "po_number": po.po_number,
        "status": po.status,
        "hitl_pending": True,
        "vendor": {"vendor_code": vendor.vendor_code, "name": vendor.name},
        "total_value": str(po.total_value),
        "currency": po.currency,
        "is_sole_source": po.is_sole_source,
        "policy_citations": citations,
        "policy_assessment": compliance.get("assessment") if isinstance(compliance, dict) else None,
        "ai_risk_summary": risk_summary,
        "approval_routing": [
            {"approver_tier": a.approver_tier, "assigned_to": a.assigned_to, "request_id": a.request_id}
            for a in approvals
        ],
        "message": "Purchase order created and routed for approval.",
    }


# ---------------------------------------------------------------------------
# 5. check_po_status
# ---------------------------------------------------------------------------

@tool_errors
def check_po_status(po_number):
    """Return a PO's lifecycle state plus a timeline of its audit events."""
    from procurement.models import AuditLedger, PurchaseOrder

    po = PurchaseOrder.objects.filter(po_number=po_number).select_related("vendor").first()
    if po is None:
        return {"error": f"Purchase order not found: {po_number}"}

    events = AuditLedger.objects.filter(
        entity_type="purchase_order", entity_id=po_number
    ).order_by("created_at")
    timeline = [
        {"action": e.action, "actor": e.actor, "at": e.created_at.isoformat()}
        for e in events
    ]
    lines = [
        {
            "material_number": li.material.material_number,
            "quantity_ordered": str(li.quantity_ordered),
            "quantity_received": str(li.quantity_received),
            "unit_price": str(li.unit_price),
        }
        for li in po.line_items.select_related("material").all()
    ]
    return {
        "po_number": po.po_number,
        "status": po.status,
        "vendor": po.vendor.name,
        "total_value": str(po.total_value),
        "currency": po.currency,
        "is_sole_source": po.is_sole_source,
        "approved_at": po.approved_at.isoformat() if po.approved_at else None,
        "sent_at": po.sent_at.isoformat() if po.sent_at else None,
        "line_items": lines,
        "timeline": timeline,
    }


# ---------------------------------------------------------------------------
# 6. record_goods_receipt
# ---------------------------------------------------------------------------

@tool_errors
def record_goods_receipt(po_number, line_items_received, quality_status, notes="", received_by="agent:mcp"):
    """Record a goods receipt against a PO and advance its lifecycle state.

    line_items_received: list of {material_number, quantity_received}.
    """
    from django_fsm import TransitionNotAllowed

    from procurement.models import GoodsReceipt, GRLineItem, PurchaseOrder

    po = PurchaseOrder.objects.filter(po_number=po_number).first()
    if po is None:
        return {"error": f"Purchase order not found: {po_number}"}

    valid_quality = {c for c, _ in GoodsReceipt.QualityStatus.choices}
    if quality_status not in valid_quality:
        return {"error": f"Invalid quality_status. Choose one of: {sorted(valid_quality)}"}

    with transaction.atomic():
        gr = GoodsReceipt.objects.create(
            po=po,
            received_by=received_by,
            quality_status=quality_status,
            quality_notes=notes or "",
        )
        recorded = []
        for item in line_items_received or []:
            po_line = po.line_items.filter(
                material__material_number=item.get("material_number")
            ).first()
            if po_line is None:
                continue
            qty = _dec(item.get("quantity_received", 0))
            GRLineItem.objects.create(gr=gr, po_line=po_line, quantity_received=qty)
            po_line.quantity_received = (po_line.quantity_received or Decimal("0")) + qty
            po_line.save(update_fields=["quantity_received"])
            recorded.append({"material_number": po_line.material.material_number, "quantity_received": str(qty)})

        transition_note = None
        try:
            po.record_receipt()
            po.save()
        except TransitionNotAllowed:
            transition_note = (
                f"PO in '{po.status}' state does not accept receipts; goods receipt "
                "recorded without a state change."
            )

    return {
        "gr_number": gr.gr_number,
        "po_number": po.po_number,
        "po_status": po.status,
        "quality_status": gr.quality_status,
        "lines_recorded": recorded,
        "note": transition_note,
    }


# ---------------------------------------------------------------------------
# 7. match_invoice_to_po
# ---------------------------------------------------------------------------

@tool_errors
def match_invoice_to_po(invoice_number, po_number, invoice_amount, vendor_invoice_ref="", invoice_date=None, due_date=None):
    """Perform a three-way match (PO × GR × invoice) and flag discrepancies."""
    from procurement.models import Invoice, PurchaseOrder

    po = PurchaseOrder.objects.filter(po_number=po_number).first()
    if po is None:
        return {"error": f"Purchase order not found: {po_number}"}

    invoice_amount = _dec(invoice_amount)
    po_value = sum(
        (li.quantity_ordered * li.unit_price for li in po.line_items.all()), Decimal("0")
    )
    received_value = sum(
        (li.quantity_received * li.unit_price for li in po.line_items.all()), Decimal("0")
    )

    discrepancies = []
    variance = invoice_amount - po_value
    abs_variance = abs(variance)
    pct_variance = (abs_variance / po_value) if po_value else Decimal("0")
    if abs_variance > MATCH_TOLERANCE_ABS and pct_variance > MATCH_TOLERANCE_PCT:
        discrepancies.append(
            {
                "type": "price_variance",
                "po_value": str(po_value),
                "invoice_amount": str(invoice_amount),
                "variance": str(variance),
                "pct": f"{pct_variance:.2%}",
            }
        )
    if received_value < po_value:
        discrepancies.append(
            {
                "type": "quantity_variance",
                "po_value": str(po_value),
                "received_value": str(received_value),
                "note": "Not all ordered quantities have been received.",
            }
        )

    match_status = Invoice.MatchStatus.DISCREPANCY if discrepancies else Invoice.MatchStatus.MATCHED
    idate = _parse_date(invoice_date)
    ddate = _parse_date(due_date, idate + timedelta(days=30))

    invoice, _ = Invoice.objects.update_or_create(
        invoice_number=invoice_number,
        defaults={
            "po": po,
            "vendor_invoice_ref": vendor_invoice_ref or f"{po.vendor.vendor_code}-{invoice_number}",
            "invoice_amount": invoice_amount,
            "match_status": match_status,
            "match_discrepancies": discrepancies,
            "invoice_date": idate,
            "due_date": ddate,
        },
    )

    return {
        "invoice_number": invoice.invoice_number,
        "po_number": po.po_number,
        "match_status": invoice.match_status,
        "po_value": str(po_value),
        "received_value": str(received_value),
        "invoice_amount": str(invoice_amount),
        "discrepancies": discrepancies,
        "three_way_match_passed": not discrepancies,
    }


# ---------------------------------------------------------------------------
# 8. query_material_master
# ---------------------------------------------------------------------------

@tool_errors
def query_material_master(material_number=None, query=None, description=None, material_group=None):
    """Return material specs, stock levels, reorder point, and lead time.

    Supports exact material_number lookup (e.g. 'MAT-STL-SHT-002'), keyword search
    across material description / number (e.g. 'steel', 'sheet', 'packaging', 'paper'),
    or material_group filtering ('raw_materials', 'indirect', 'capex', 'services').
    """
    from django.db.models import Q
    from procurement.models import Material

    term = material_number or query or description
    if not term and not material_group:
        return {"error": "Please provide a material_number, search keyword, or material_group."}

    qs = Material.objects.all()

    if term:
        exact = qs.filter(material_number__iexact=str(term).strip()).first()
        if exact:
            return {
                "material_number": exact.material_number,
                "description": exact.description,
                "material_group": exact.material_group,
                "unit_of_measure": exact.unit_of_measure,
                "base_price": str(exact.base_price),
                "lead_time_days": exact.lead_time_days,
                "reorder_point": exact.reorder_point,
                "stock_qty": exact.stock_qty,
                "reorder_needed": exact.stock_qty <= exact.reorder_point,
            }

        q_filter = Q(material_number__icontains=term) | Q(description__icontains=term)
        words = [w.strip() for w in str(term).split() if len(w.strip()) > 2]
        for w in words:
            q_filter |= Q(description__icontains=w) | Q(material_number__icontains=w)
        qs = qs.filter(q_filter)

    if material_group:
        norm = str(material_group).strip().lower().replace("-", "_").replace(" ", "_")
        qs = qs.filter(material_group__icontains=norm)

    qs = qs.distinct()[:10]
    materials = [
        {
            "material_number": m.material_number,
            "description": m.description,
            "material_group": m.material_group,
            "unit_of_measure": m.unit_of_measure,
            "base_price": str(m.base_price),
            "lead_time_days": m.lead_time_days,
            "reorder_point": m.reorder_point,
            "stock_qty": m.stock_qty,
            "reorder_needed": m.stock_qty <= m.reorder_point,
        }
        for m in qs
    ]

    if not materials:
        return {
            "error": f"Material not found: {term or material_group}",
            "count": 0,
            "materials": [],
            "message": f"No materials found matching '{term or material_group}'. Try querying with terms like 'steel', 'sheet', 'paper', 'wire', or group 'raw_materials'.",
        }

    return {"count": len(materials), "materials": materials}


# Registry consumed by the MCP server and test scripts.
ALL_TOOLS = {
    "create_purchase_requisition": create_purchase_requisition,
    "search_vendors": search_vendors,
    "evaluate_vendor": evaluate_vendor,
    "create_purchase_order": create_purchase_order,
    "check_po_status": check_po_status,
    "record_goods_receipt": record_goods_receipt,
    "match_invoice_to_po": match_invoice_to_po,
    "query_material_master": query_material_master,
    "check_policy_compliance": check_policy_compliance,
    "route_for_approval": route_for_approval,
}
