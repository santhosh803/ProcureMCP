# ProcureMCP

Enterprise procurement agent platform where a policy-aware agent orchestrates the
full Procure-to-Pay lifecycle — requisition, vendor selection, PO creation, goods
receipt, and invoice matching — through natural language, with pgvector-backed
policy RAG and multi-tier human-in-the-loop approvals. The procurement operations
are exposed as MCP tools, so any MCP-capable agent can drive them.

## MCP tools (full P2P lifecycle)

| Tool | Purpose |
|---|---|
| `create_purchase_requisition` | Draft a PR with line items, cost center, and justification |
| `search_vendors` | Search vendor master by material group, score, and region |
| `evaluate_vendor` | Vendor scorecard: delivery %, quality, pricing, PO count, risk flags |
| `create_purchase_order` | Convert an approved PR → PO (runs policy RAG + approval routing) |
| `check_po_status` | PO lifecycle state with a timeline of audit events |
| `record_goods_receipt` | Log a goods receipt per line and advance PO state |
| `match_invoice_to_po` | Three-way match (PO × goods receipt × invoice), flag discrepancies |
| `query_material_master` | Material specs, stock, reorder point, lead time |
| `check_policy_compliance` | pgvector RAG query, returns cited policy snippets |
| `route_for_approval` | Multi-tier approval routing by value + category |

## Running the MCP server

```bash
# stdio (for desktop agents and local clients)
uv run python manage.py run_mcp_server

# SSE (for networked agents)
uv run python manage.py run_mcp_server --transport sse --port 8001
```

## Claude Desktop integration

Add the server to your Claude Desktop config
(`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "procuremcp": {
      "command": "uv",
      "args": ["run", "python", "manage.py", "run_mcp_server"],
      "cwd": "/absolute/path/to/ProcureMCP",
      "env": {
        "DJANGO_SETTINGS_MODULE": "procuremcp.settings"
      }
    }
  }
}
```

Restart Claude Desktop; the 10 procurement tools become available and run against
the same Django backend as the internal agent — demonstrating that the MCP layer
is agent-framework agnostic.
