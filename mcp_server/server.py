"""ProcureMCP MCP server.

Exposes the 10 Procure-to-Pay tools over stdio (for Claude Desktop and local
agents) and SSE (for networked agents such as the internal LangGraph
orchestrator). Django is initialised at import time so tools can use the ORM.
"""

import argparse
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "procuremcp.settings")

# Point the Google client at the service-account file if a relative path is set.
_cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
if _cred and not os.path.isabs(_cred):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(_cred)

django.setup()

from mcp.server.mcpserver import MCPServer  # noqa: E402

from mcp_server import tools  # noqa: E402

mcp = MCPServer(
    name="ProcureMCP",
    instructions=(
        "Enterprise procurement (Procure-to-Pay) tools. Before recommending or "
        "executing any purchase, verify the vendor is approved, check policy "
        "compliance, and never bypass approval routing. Approval-gated operations "
        "return hitl_pending and must be surfaced to a human approver."
    ),
)


@mcp.tool()
def create_purchase_requisition(
    requester: str, cost_center: str, justification: str, line_items: list
) -> dict:
    """Draft a purchase requisition. line_items: list of {material_number, quantity, estimated_unit_price?, delivery_date_needed?}."""
    return tools.create_purchase_requisition(requester, cost_center, justification, line_items)


@mcp.tool()
def search_vendors(
    material_group: str = "", min_score: float = 0.0, region: str = "", limit: int = 10
) -> dict:
    """Search approved vendors filtered by material group, minimum overall score, and region."""
    return tools.search_vendors(material_group or None, min_score, region or None, limit)


@mcp.tool()
def evaluate_vendor(vendor_code: str) -> dict:
    """Return a vendor scorecard: delivery %, quality, pricing, PO count, risk flags, recommendation."""
    return tools.evaluate_vendor(vendor_code)


@mcp.tool()
def create_purchase_order(
    pr_number: str,
    vendor_code: str,
    is_sole_source: bool = False,
    sole_source_justification: str = "",
) -> dict:
    """Convert an approved requisition into a purchase order; runs policy RAG and routes for approval (returns hitl_pending)."""
    return tools.create_purchase_order(
        pr_number, vendor_code, is_sole_source, sole_source_justification
    )


@mcp.tool()
def check_po_status(po_number: str) -> dict:
    """Return a purchase order's lifecycle state and a timeline of its audit events."""
    return tools.check_po_status(po_number)


@mcp.tool()
def record_goods_receipt(
    po_number: str,
    line_items_received: list,
    quality_status: str,
    notes: str = "",
) -> dict:
    """Record a goods receipt (line_items_received: list of {material_number, quantity_received}) and advance PO state."""
    return tools.record_goods_receipt(po_number, line_items_received, quality_status, notes)


@mcp.tool()
def match_invoice_to_po(
    invoice_number: str,
    po_number: str,
    invoice_amount: float,
    vendor_invoice_ref: str = "",
) -> dict:
    """Perform a three-way match (PO × goods receipt × invoice) and flag discrepancies."""
    return tools.match_invoice_to_po(
        invoice_number, po_number, invoice_amount, vendor_invoice_ref
    )


@mcp.tool()
def query_material_master(material_number: str) -> dict:
    """Look up a material's specs, stock level, reorder point, and lead time."""
    return tools.query_material_master(material_number)


@mcp.tool()
def check_policy_compliance(
    query: str, entity_type: str = "", entity_value: float = None
) -> dict:
    """RAG query against the policy corpus; returns cited policy snippets and a compliance assessment."""
    return tools.check_policy_compliance(query, entity_type or None, entity_value)


@mcp.tool()
def route_for_approval(
    entity_type: str, entity_id: str, value: float, is_sole_source: bool = False
) -> dict:
    """Route an entity to the correct approval tier by value (and sole-source committee); returns hitl_pending."""
    return tools.route_for_approval(entity_type, entity_id, value, is_sole_source)


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Run the ProcureMCP MCP server.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport to serve on (default: stdio).",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host for SSE/HTTP transport.")
    parser.add_argument("--port", type=int, default=8001, help="Port for SSE/HTTP transport.")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
