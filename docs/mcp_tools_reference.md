# MCP Tools Reference

All tools return a JSON-serialisable dict. Errors are returned as
`{"error": "..."}` rather than raised. Approval-gated tools return
`hitl_pending: true` with an `ai_risk_summary`.

### create_purchase_requisition
`requester: str, cost_center: str, justification: str, line_items: list`
Each line item: `{material_number, quantity, estimated_unit_price?, delivery_date_needed?}`.
Missing prices default to the material base price; missing dates to today + lead time.

### search_vendors
`material_group?: str, min_score?: float, region?: str, limit?: int`
Returns vendors ordered by descending overall score.

### evaluate_vendor
`vendor_code: str`
Returns the scorecard (on-time %, quality, price, overall), actual PO count,
risk flags, and a recommendation.

### create_purchase_order
`pr_number: str, vendor_code: str, is_sole_source?: bool, sole_source_justification?: str`
Converts an approved requisition into a PO, runs the policy RAG check (citations
stored on the PO), submits for approval, and returns `hitl_pending` with the
approval routing. Blacklisted vendors are rejected.

### check_po_status
`po_number: str`
Returns the lifecycle state, line items, and a timeline of audit events.

### record_goods_receipt
`po_number: str, line_items_received: list, quality_status: str, notes?: str`
Each line: `{material_number, quantity_received}`. `quality_status` ∈
`accepted | rejected | partial`. Advances the PO state (partially/fully received).

### match_invoice_to_po
`invoice_number: str, po_number: str, invoice_amount: number, vendor_invoice_ref?: str`
Three-way match. Flags a discrepancy when the price variance exceeds both 2% and
$100, or when received quantity is short of ordered.

### query_material_master
`material_number: str`
Returns specs, stock, reorder point, lead time, and whether a reorder is needed.

### check_policy_compliance
`query: str, entity_type?: str, entity_value?: number`
RAG retrieval over the policy corpus. Returns cited snippets with similarity
scores, the required approval tier for the value, and an assessment.

### route_for_approval
`entity_type: str, entity_id: str, value: number, is_sole_source?: bool`
Creates the value-based ApprovalRequest and, for sole-source, a parallel
committee request. Returns `hitl_pending` with all created requests.
