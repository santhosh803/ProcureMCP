"""Populate a realistic procurement dataset for development and demos.

Generates material and vendor master data, policy documents, requisitions,
purchase orders across every lifecycle state (including sole-source), goods
receipts, invoices with matched/discrepancy outcomes, and approval requests in
mixed decision states. Idempotent: pass --flush to wipe procurement data first.
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone
from faker import Faker

from procurement.models import (
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

fake = Faker()
Faker.seed(42)
random.seed(42)

MATERIAL_GROUPS = ["raw_materials", "indirect", "services", "capex"]
UOM_BY_GROUP = {
    "raw_materials": ["KG", "L", "EA"],
    "indirect": ["EA", "BOX"],
    "services": ["HR", "DAY"],
    "capex": ["EA"],
}

RAW_ITEMS = [
    ("Steel Bolt M8x40", "STL-BLT"),
    ("Stainless Sheet 2mm", "STL-SHT"),
    ("Copper Wire 4mm", "CU-WIR"),
    ("Aluminium Ingot", "AL-ING"),
    ("Industrial Lubricant", "LUB-OIL"),
    ("Polymer Resin", "POL-RES"),
    ("Rubber Gasket", "RUB-GSK"),
    ("Welding Rod", "WLD-ROD"),
    ("Carbon Fibre Roll", "CF-ROL"),
    ("Silicone Sealant", "SIL-SEA"),
]
INDIRECT_ITEMS = [
    ("A4 Paper Ream", "OFF-PPR"),
    ("Nitrile Gloves Box", "SAF-GLV"),
    ("Safety Helmet", "SAF-HLM"),
    ("Cleaning Detergent", "JAN-DET"),
    ("Printer Toner", "OFF-TNR"),
    ("LED Tube Light", "ELC-LED"),
    ("Cable Ties Pack", "ELC-CTP"),
    ("First Aid Kit", "SAF-FAK"),
    ("Ballpoint Pen Box", "OFF-PEN"),
    ("Desk Sanitiser", "JAN-SAN"),
]
SERVICE_ITEMS = [
    ("HVAC Maintenance", "SRV-HVC"),
    ("Facility Cleaning", "SRV-CLN"),
    ("IT Support Retainer", "SRV-ITS"),
    ("Machinery Calibration", "SRV-CAL"),
    ("Security Patrol", "SRV-SEC"),
    ("Waste Disposal", "SRV-WST"),
    ("Consulting Advisory", "SRV-CON"),
    ("Software License Support", "SRV-LIC"),
    ("Equipment Rental", "SRV-RNT"),
    ("Logistics Freight", "SRV-FRT"),
]
CAPEX_ITEMS = [
    ("CNC Milling Machine", "CAP-CNC"),
    ("Forklift Truck", "CAP-FLT"),
    ("Server Rack Unit", "CAP-SRV"),
    ("Industrial Compressor", "CAP-CMP"),
    ("Laser Cutter", "CAP-LSR"),
    ("Cold Storage Unit", "CAP-CLD"),
    ("Conveyor System", "CAP-CNV"),
    ("Backup Generator", "CAP-GEN"),
    ("Robotic Arm", "CAP-ROB"),
    ("Packaging Line", "CAP-PKG"),
]

GROUP_ITEMS = {
    "raw_materials": RAW_ITEMS,
    "indirect": INDIRECT_ITEMS,
    "services": SERVICE_ITEMS,
    "capex": CAPEX_ITEMS,
}

PRICE_RANGE = {
    "raw_materials": (5, 400),
    "indirect": (2, 120),
    "services": (150, 4000),
    "capex": (8000, 90000),
}

POLICIES = [
    ("Manager Approval Threshold", "spending_limit",
     "Purchase orders with a total value below USD 10,000 require approval only "
     "from the requesting employee's line manager. This tier-one approval must be "
     "recorded in the procurement system before any order is transmitted to a vendor."),
    ("Finance Approval Threshold", "spending_limit",
     "Purchase orders with a total value of USD 10,000 or greater but below USD "
     "50,000 require finance department approval in addition to manager sign-off. "
     "Finance reviews budget availability and vendor payment terms."),
    ("CFO Approval Threshold", "spending_limit",
     "Any purchase order with a total value of USD 50,000 or greater must be "
     "escalated to the Chief Financial Officer for approval. The CFO reviews "
     "capital impact, cash flow timing, and strategic alignment before release."),
    ("Indirect Materials Spending Cap", "category_rule",
     "Indirect material purchases are capped at USD 25,000 per cost center per "
     "quarter. Requisitions exceeding this cap require category-manager review and "
     "documented budget justification."),
    ("Capex Purchase Governance", "category_rule",
     "All capital expenditure (capex) purchases must reference an approved capital "
     "budget line and include a return-on-investment justification. Capex orders "
     "always route to finance regardless of value."),
    ("Approved Vendor Requirement", "approved_vendor",
     "Purchase orders may only be placed with vendors holding 'approved' status in "
     "the vendor master. Vendors marked 'under_review' require procurement lead "
     "authorisation; 'blacklisted' vendors are strictly prohibited."),
    ("Vendor Performance Minimum", "approved_vendor",
     "For purchase orders above USD 20,000, the selected vendor must maintain an "
     "overall performance score of at least 3.0 out of 5.0. Lower-scoring vendors "
     "require a documented exception approved by the category manager."),
    ("Sole-Source Justification", "sole_source",
     "Single-source or sole-source purchases that bypass competitive bidding must "
     "include a written justification and are routed in parallel to the sole-source "
     "review committee in addition to the standard value-based approval tier."),
    ("Competitive Bidding Requirement", "sole_source",
     "Purchases of USD 30,000 or more must solicit at least three competitive "
     "quotations unless a valid sole-source justification is on file and approved "
     "by the sole-source committee."),
    ("Three-Way Match Policy", "compliance",
     "Vendor invoices must pass a three-way match against the purchase order and "
     "goods receipt before payment. Quantity and price variances beyond 2 percent "
     "or USD 100 are flagged as discrepancies requiring manual resolution."),
    ("Segregation of Duties", "compliance",
     "The employee who creates a purchase requisition may not approve the resulting "
     "purchase order. Approval and goods-receipt confirmation must be performed by "
     "different individuals to preserve segregation of duties."),
    ("Payment Terms Standard", "category_rule",
     "The default payment term for new vendors is NET30. Extended terms such as "
     "NET60 require finance approval. Early-payment discount terms such as 2/10 "
     "NET30 should be prioritised where cash flow permits."),
    ("Emergency Purchase Protocol", "general",
     "Emergency purchases required to prevent production stoppage may proceed with "
     "verbal manager approval, but must be formalised with a retroactive purchase "
     "order and written justification within 48 hours."),
    ("Raw Materials Reorder Rule", "category_rule",
     "Raw materials that fall at or below their reorder point should trigger a "
     "replenishment requisition. Reorder quantities should account for supplier "
     "lead time and current stock on hand."),
    ("Data Retention and Audit", "compliance",
     "All procurement transactions, approvals, and policy citations must be retained "
     "in the immutable audit ledger for a minimum of seven years to satisfy audit "
     "and regulatory requirements."),
]


class Command(BaseCommand):
    help = "Seed the procurement database with realistic sample data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing procurement data before seeding.",
        )

    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()

        materials = self._seed_materials()
        vendors = self._seed_vendors()
        self._seed_policies()
        prs = self._seed_requisitions(materials)
        pos = self._seed_purchase_orders(materials, vendors, prs)
        self._seed_goods_receipts(pos)
        self._seed_invoices(pos)
        self._seed_approval_requests(pos, prs)

        self.stdout.write(self.style.SUCCESS("Seed data created successfully."))
        self._summary()

    # -- flush ---------------------------------------------------------------

    def _flush(self):
        self.stdout.write("Flushing existing procurement data…")
        for model in (
            AuditLedger,
            Invoice,
            GRLineItem,
            GoodsReceipt,
            ApprovalRequest,
            POLineItem,
            PurchaseOrder,
            PRLineItem,
            PurchaseRequisition,
            PolicyDocument,
            VendorMaster,
            Material,
        ):
            model.objects.all().delete()

    # -- materials -----------------------------------------------------------

    def _seed_materials(self):
        materials = []
        counter = 1
        for group in MATERIAL_GROUPS:
            for desc, code in GROUP_ITEMS[group]:
                low, high = PRICE_RANGE[group]
                price = Decimal(random.uniform(low, high)).quantize(Decimal("0.01"))
                uom = random.choice(UOM_BY_GROUP[group])
                reorder = random.randint(10, 200) if group in ("raw_materials", "indirect") else 0
                stock = random.randint(0, 400) if reorder else random.randint(0, 5)
                material = Material.objects.create(
                    material_number=f"MAT-{code}-{counter:03d}",
                    description=desc,
                    material_group=group,
                    unit_of_measure=uom,
                    base_price=price,
                    lead_time_days=random.choice([3, 5, 7, 14, 21, 30]),
                    reorder_point=reorder,
                    stock_qty=stock,
                )
                materials.append(material)
                counter += 1
        self.stdout.write(f"  materials: {len(materials)}")
        return materials

    # -- vendors -------------------------------------------------------------

    def _seed_vendors(self):
        vendors = []
        statuses = (
            [VendorMaster.Status.APPROVED] * 19
            + [VendorMaster.Status.UNDER_REVIEW] * 4
            + [VendorMaster.Status.BLACKLISTED] * 2
        )
        random.shuffle(statuses)
        for i in range(1, 26):
            status = statuses[i - 1]
            groups = random.sample(MATERIAL_GROUPS, random.randint(1, 3))
            on_time = round(random.uniform(60, 99), 1)
            quality = round(random.uniform(2.0, 5.0), 2)
            price = round(random.uniform(2.0, 5.0), 2)
            overall = round(
                (on_time / 100.0) * 5.0 * 0.4 + quality * 0.35 + price * 0.25, 2
            )
            vendor = VendorMaster.objects.create(
                vendor_code=f"VEN-{i:05d}",
                name=fake.company(),
                country=fake.country(),
                payment_terms=random.choice(["NET30", "NET60", "2/10 NET30"]),
                material_groups=groups,
                status=status,
                on_time_delivery_pct=on_time,
                quality_rating=quality,
                price_competitiveness=price,
                overall_score=overall,
                total_orders=random.randint(0, 120),
                last_evaluated_at=timezone.now() - timedelta(days=random.randint(1, 90)),
            )
            vendors.append(vendor)
        self.stdout.write(f"  vendors: {len(vendors)}")
        return vendors

    # -- policies ------------------------------------------------------------

    def _seed_policies(self):
        for title, ptype, content in POLICIES:
            PolicyDocument.objects.create(
                title=title,
                policy_type=ptype,
                content=content,
                version="1.0",
                effective_date=timezone.now().date() - timedelta(days=random.randint(30, 365)),
            )
        self.stdout.write(f"  policies: {len(POLICIES)}")

    # -- requisitions --------------------------------------------------------

    def _seed_requisitions(self, materials):
        prs = []
        statuses = (
            [PurchaseRequisition.Status.DRAFT] * 4
            + [PurchaseRequisition.Status.SUBMITTED] * 6
            + [PurchaseRequisition.Status.APPROVED] * 5
            + [PurchaseRequisition.Status.REJECTED] * 2
            + [PurchaseRequisition.Status.CONVERTED_TO_PO] * 3
        )
        random.shuffle(statuses)
        for i in range(20):
            status = statuses[i]
            created = timezone.now() - timedelta(days=random.randint(1, 120))
            pr = PurchaseRequisition.objects.create(
                requester=fake.email(),
                cost_center=f"CC-{random.randint(1000, 1999)}",
                justification=fake.paragraph(nb_sentences=2),
                status=status,
                submitted_at=created if status != PurchaseRequisition.Status.DRAFT else None,
            )
            total = Decimal("0")
            for _ in range(random.randint(1, 4)):
                material = random.choice(materials)
                qty = Decimal(random.randint(1, 50))
                unit = material.base_price
                PRLineItem.objects.create(
                    pr=pr,
                    material=material,
                    quantity=qty,
                    estimated_unit_price=unit,
                    delivery_date_needed=(created + timedelta(days=material.lead_time_days)).date(),
                )
                total += qty * unit
            pr.total_value = total
            pr.save(update_fields=["total_value"])
            prs.append(pr)
        self.stdout.write(f"  requisitions: {len(prs)}")
        return prs

    # -- purchase orders -----------------------------------------------------

    def _seed_purchase_orders(self, materials, vendors, prs):
        approved_vendors = [v for v in vendors if v.status == VendorMaster.Status.APPROVED]
        lifecycle = (
            [PurchaseOrder.Status.DRAFT] * 3
            + [PurchaseOrder.Status.PENDING_APPROVAL] * 5
            + [PurchaseOrder.Status.APPROVED] * 4
            + [PurchaseOrder.Status.SENT_TO_VENDOR] * 4
            + [PurchaseOrder.Status.PARTIALLY_RECEIVED] * 3
            + [PurchaseOrder.Status.FULLY_RECEIVED] * 4
            + [PurchaseOrder.Status.INVOICED] * 3
            + [PurchaseOrder.Status.CLOSED] * 3
            + [PurchaseOrder.Status.CANCELLED] * 1
        )
        random.shuffle(lifecycle)
        pos = []
        convertible_prs = [p for p in prs if p.status == PurchaseRequisition.Status.CONVERTED_TO_PO]
        for i in range(30):
            status = lifecycle[i]
            vendor = random.choice(approved_vendors)
            created = timezone.now() - timedelta(days=random.randint(1, 100))
            is_sole = random.random() < 0.2
            source_pr = convertible_prs[i % len(convertible_prs)] if convertible_prs and random.random() < 0.4 else None
            po = PurchaseOrder.objects.create(
                source_pr=source_pr,
                vendor=vendor,
                total_value=Decimal("0"),
                status=status,
                is_sole_source=is_sole,
                sole_source_justification=(
                    fake.sentence(nb_words=12) if is_sole else ""
                ),
                approved_at=created + timedelta(days=1)
                if status not in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.PENDING_APPROVAL)
                else None,
                sent_at=created + timedelta(days=2)
                if status in (
                    PurchaseOrder.Status.SENT_TO_VENDOR,
                    PurchaseOrder.Status.PARTIALLY_RECEIVED,
                    PurchaseOrder.Status.FULLY_RECEIVED,
                    PurchaseOrder.Status.INVOICED,
                    PurchaseOrder.Status.CLOSED,
                )
                else None,
            )
            total = Decimal("0")
            received_state = status in (
                PurchaseOrder.Status.PARTIALLY_RECEIVED,
                PurchaseOrder.Status.FULLY_RECEIVED,
                PurchaseOrder.Status.INVOICED,
                PurchaseOrder.Status.CLOSED,
            )
            for _ in range(random.randint(1, 4)):
                material = random.choice(materials)
                qty = Decimal(random.randint(1, 40))
                unit = material.base_price
                if status == PurchaseOrder.Status.FULLY_RECEIVED or status in (
                    PurchaseOrder.Status.INVOICED,
                    PurchaseOrder.Status.CLOSED,
                ):
                    received = qty
                elif status == PurchaseOrder.Status.PARTIALLY_RECEIVED:
                    received = (qty / 2).quantize(Decimal("0.01"))
                else:
                    received = Decimal("0")
                POLineItem.objects.create(
                    po=po,
                    material=material,
                    quantity_ordered=qty,
                    quantity_received=received,
                    unit_price=unit,
                    delivery_date=(created + timedelta(days=material.lead_time_days)).date(),
                )
                total += qty * unit
            po.total_value = total
            po.save(update_fields=["total_value"])
            pos.append(po)
        self.stdout.write(f"  purchase orders: {len(pos)}")
        return pos

    # -- goods receipts ------------------------------------------------------

    def _seed_goods_receipts(self, pos):
        receiving_pos = [
            p for p in pos
            if p.status in (
                PurchaseOrder.Status.PARTIALLY_RECEIVED,
                PurchaseOrder.Status.FULLY_RECEIVED,
                PurchaseOrder.Status.INVOICED,
                PurchaseOrder.Status.CLOSED,
            )
        ]
        count = 0
        for po in receiving_pos[:15]:
            quality = random.choices(
                [
                    GoodsReceipt.QualityStatus.ACCEPTED,
                    GoodsReceipt.QualityStatus.PARTIAL,
                    GoodsReceipt.QualityStatus.REJECTED,
                ],
                weights=[7, 2, 1],
            )[0]
            gr = GoodsReceipt.objects.create(
                po=po,
                received_by=fake.name(),
                quality_status=quality,
                quality_notes=fake.sentence(nb_words=8) if quality != GoodsReceipt.QualityStatus.ACCEPTED else "",
            )
            for po_line in po.line_items.all():
                if po_line.quantity_received and po_line.quantity_received > 0:
                    GRLineItem.objects.create(
                        gr=gr,
                        po_line=po_line,
                        quantity_received=po_line.quantity_received,
                    )
            count += 1
        self.stdout.write(f"  goods receipts: {count}")

    # -- invoices ------------------------------------------------------------

    def _seed_invoices(self, pos):
        invoiceable = [
            p for p in pos
            if p.status in (
                PurchaseOrder.Status.INVOICED,
                PurchaseOrder.Status.CLOSED,
                PurchaseOrder.Status.FULLY_RECEIVED,
            )
        ]
        count = 0
        for idx, po in enumerate(invoiceable[:10], start=1):
            discrepancy = random.random() < 0.3
            amount = po.total_value
            discrepancies = []
            if discrepancy:
                delta = (po.total_value * Decimal("0.06")).quantize(Decimal("0.01"))
                amount = po.total_value + delta
                discrepancies = [
                    {
                        "type": "price_variance",
                        "expected": str(po.total_value),
                        "invoiced": str(amount),
                        "variance": str(delta),
                    }
                ]
                match_status = Invoice.MatchStatus.DISCREPANCY
            else:
                match_status = random.choice(
                    [
                        Invoice.MatchStatus.MATCHED,
                        Invoice.MatchStatus.APPROVED_FOR_PAYMENT,
                        Invoice.MatchStatus.PAID,
                    ]
                )
            invoice_date = timezone.now().date() - timedelta(days=random.randint(1, 40))
            Invoice.objects.create(
                invoice_number=f"INV-{timezone.now().year}-{idx:04d}",
                po=po,
                vendor_invoice_ref=f"{po.vendor.vendor_code}-{fake.bothify('??-#####')}",
                invoice_amount=amount,
                match_status=match_status,
                match_discrepancies=discrepancies,
                invoice_date=invoice_date,
                due_date=invoice_date + timedelta(days=30),
            )
            count += 1
        self.stdout.write(f"  invoices: {count}")

    # -- approval requests ---------------------------------------------------

    def _seed_approval_requests(self, pos, prs):
        def tier_for(value):
            if value < 10000:
                return ApprovalRequest.ApproverTier.MANAGER
            if value < 50000:
                return ApprovalRequest.ApproverTier.FINANCE
            return ApprovalRequest.ApproverTier.CFO

        approvers = {
            ApprovalRequest.ApproverTier.MANAGER: "manager@procuremcp.example",
            ApprovalRequest.ApproverTier.FINANCE: "finance@procuremcp.example",
            ApprovalRequest.ApproverTier.CFO: "cfo@procuremcp.example",
            ApprovalRequest.ApproverTier.SOLE_SOURCE_COMMITTEE: "committee@procuremcp.example",
        }
        decisions = (
            [ApprovalRequest.Decision.PENDING] * 5
            + [ApprovalRequest.Decision.APPROVED] * 4
            + [ApprovalRequest.Decision.REJECTED] * 2
            + [ApprovalRequest.Decision.ESCALATED] * 1
        )
        random.shuffle(decisions)
        candidates = [p for p in pos if p.status in (
            PurchaseOrder.Status.PENDING_APPROVAL,
            PurchaseOrder.Status.APPROVED,
            PurchaseOrder.Status.SENT_TO_VENDOR,
        )]
        if len(candidates) < 12:
            candidates = candidates + [p for p in pos if p not in candidates]
        count = 0
        for i in range(12):
            po = candidates[i % len(candidates)]
            tier = tier_for(float(po.total_value))
            decision = decisions[i]
            decided = decision != ApprovalRequest.Decision.PENDING
            ApprovalRequest.objects.create(
                entity_type="purchase_order",
                entity_id=po.po_number,
                approver_tier=tier,
                assigned_to=approvers[tier],
                decision=decision,
                decision_reason=fake.sentence(nb_words=10) if decided else "",
                ai_risk_summary=(
                    f"PO value {po.total_value} routed to {tier}. "
                    f"Vendor {po.vendor.name} scored {po.vendor.overall_score}/5.0. "
                    f"{'Sole-source purchase — competitive bidding bypassed.' if po.is_sole_source else 'Standard competitive purchase.'}"
                ),
                decided_at=timezone.now() if decided else None,
            )
            count += 1
            if po.is_sole_source:
                ApprovalRequest.objects.create(
                    entity_type="purchase_order",
                    entity_id=po.po_number,
                    approver_tier=ApprovalRequest.ApproverTier.SOLE_SOURCE_COMMITTEE,
                    assigned_to=approvers[ApprovalRequest.ApproverTier.SOLE_SOURCE_COMMITTEE],
                    decision=ApprovalRequest.Decision.PENDING,
                    ai_risk_summary="Parallel sole-source committee review required.",
                )
                count += 1
        self.stdout.write(f"  approval requests: {count}")

    # -- summary -------------------------------------------------------------

    def _summary(self):
        self.stdout.write("")
        self.stdout.write("Current row counts:")
        for label, model in (
            ("Materials", Material),
            ("Vendors", VendorMaster),
            ("Policy documents", PolicyDocument),
            ("Requisitions", PurchaseRequisition),
            ("Purchase orders", PurchaseOrder),
            ("Goods receipts", GoodsReceipt),
            ("Invoices", Invoice),
            ("Approval requests", ApprovalRequest),
            ("Audit ledger", AuditLedger),
        ):
            self.stdout.write(f"  {label}: {model.objects.count()}")
