"""MCP client wrapper exposing the procurement tools as LangChain tools.

Two transports are supported, proving the MCP layer is framework-agnostic:

* ``local`` (default) — wraps the in-process tool implementations directly, so
  the internal agent needs no separate server process. Reliable and fast.
* ``sse`` — connects to a running ProcureMCP MCP server over SSE via
  ``langchain-mcp-adapters``, the same server Claude Desktop consumes.

Select with the ``AGENT_MCP_TRANSPORT`` env var (``local`` | ``sse``).
"""

import os

from langchain_core.tools import StructuredTool

from mcp_server import tools as tool_impls


def _make_local_tools():
    """Wrap each MCP tool implementation as a LangChain StructuredTool."""

    def wrap(name, func, description):
        return StructuredTool.from_function(
            func=func, name=name, description=description
        )

    specs = [
        ("create_purchase_requisition", tool_impls.create_purchase_requisition,
         "Draft a purchase requisition. Args: requester (str), cost_center (str), "
         "justification (str), line_items (list of {material_number, quantity, "
         "estimated_unit_price?, delivery_date_needed?})."),
        ("search_vendors", tool_impls.search_vendors,
         "Search approved vendors. Args: material_group (str, optional), min_score "
         "(float, optional), region (str, optional), limit (int, optional)."),
        ("evaluate_vendor", tool_impls.evaluate_vendor,
         "Return a vendor scorecard and risk flags. Args: vendor_code (str)."),
        ("create_purchase_order", tool_impls.create_purchase_order,
         "Convert an approved requisition into a purchase order; runs policy RAG and "
         "routes for approval (returns hitl_pending). Args: pr_number (str), "
         "vendor_code (str), is_sole_source (bool, optional), "
         "sole_source_justification (str, optional)."),
        ("check_po_status", tool_impls.check_po_status,
         "Return a purchase order's status and audit timeline. Args: po_number (str)."),
        ("record_goods_receipt", tool_impls.record_goods_receipt,
         "Record a goods receipt and advance PO state. Args: po_number (str), "
         "line_items_received (list of {material_number, quantity_received}), "
         "quality_status (str: accepted|rejected|partial), notes (str, optional)."),
        ("match_invoice_to_po", tool_impls.match_invoice_to_po,
         "Three-way match of PO, goods receipt, and invoice. Args: invoice_number "
         "(str), po_number (str), invoice_amount (number), vendor_invoice_ref "
         "(str, optional)."),
        ("query_material_master", tool_impls.query_material_master,
         "Look up material specs, stock, reorder point, lead time. Args: "
         "material_number (str)."),
        ("check_policy_compliance", tool_impls.check_policy_compliance,
         "RAG query against the policy corpus; returns cited policy snippets. Args: "
         "query (str), entity_type (str, optional), entity_value (number, optional)."),
        ("route_for_approval", tool_impls.route_for_approval,
         "Route an entity to the correct approval tier. Args: entity_type (str), "
         "entity_id (str), value (number), is_sole_source (bool, optional)."),
    ]
    return [wrap(n, f, d) for n, f, d in specs]


_local_tools_cache = None


def get_local_tools():
    global _local_tools_cache
    if _local_tools_cache is None:
        _local_tools_cache = _make_local_tools()
    return _local_tools_cache


async def get_sse_tools(url=None):
    """Load tools from a running MCP server over SSE (framework-agnostic path)."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    url = url or os.environ.get("MCP_SSE_URL", "http://127.0.0.1:8001/sse")
    client = MultiServerMCPClient(
        {"procuremcp": {"transport": "sse", "url": url}}
    )
    return await client.get_tools()


def get_tools():
    """Return LangChain tools for the configured transport (default: local)."""
    transport = os.environ.get("AGENT_MCP_TRANSPORT", "local").lower()
    if transport == "sse":
        import asyncio

        return asyncio.run(get_sse_tools())
    return get_local_tools()
