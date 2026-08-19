"""LangGraph nodes: retrieve_policy_context -> reason -> act -> hitl_check.

This is the RAG-in-the-loop pattern: policy context is retrieved and injected
BEFORE the model reasons about which tools to call, and any tool result carrying
``hitl_pending`` pauses the graph for human approval.
"""

import json
import logging
import os

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.types import interrupt

from .mcp_client import get_tools
from .prompts import SYSTEM_PROMPT, format_policy_context

logger = logging.getLogger(__name__)

_llm = None
_tools_by_name = None


def _get_llm_with_tools():
    global _llm, _tools_by_name
    if _llm is None:
        from langchain_google_vertexai import ChatVertexAI

        cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if cred and not os.path.isabs(cred):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(cred)

        tools = get_tools()
        _tools_by_name = {t.name: t for t in tools}
        base = ChatVertexAI(
            model=os.environ.get("AGENT_MODEL", "gemini-2.5-pro"),
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            temperature=0,
            max_retries=2,
        )
        _llm = base.bind_tools(tools)
    return _llm, _tools_by_name


def _latest_user_text(messages):
    for msg in reversed(messages):
        msg_type = getattr(msg, "type", None)
        if msg_type == "human":
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def retrieve_policy_context_node(state):
    """Fetch top-k policy snippets for the latest user request and inject them."""
    query = _latest_user_text(state["messages"])
    snippets = []
    if query:
        try:
            from .retriever import retrieve_policy_context

            snippets = retrieve_policy_context(query, k=5)
        except Exception:  # noqa: BLE001 - retrieval is best-effort; reasoning continues
            logger.warning("Policy retrieval failed; continuing without context.")

    new_messages = []
    context_block = format_policy_context(snippets)
    if context_block:
        new_messages.append(SystemMessage(content=context_block))
    return {"policy_context": snippets, "messages": new_messages}


def reason_node(state):
    """Gemini 2.5 Pro decides the next tool call (or a final answer)."""
    llm, _ = _get_llm_with_tools()
    messages = state["messages"]
    if not any(getattr(m, "type", None) == "system" and SYSTEM_PROMPT[:24] in str(m.content)
               for m in messages):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
    response = llm.invoke(messages)
    return {"messages": [response]}


def act_node(state):
    """Execute the tool calls requested by the model."""
    _, tools_by_name = _get_llm_with_tools()
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None) or []

    tool_messages = []
    hitl_pending = False
    hitl_payload = {}
    for call in tool_calls:
        name = call["name"]
        args = call.get("args", {}) or {}
        tool = tools_by_name.get(name)
        if tool is None:
            result = {"error": f"Unknown tool: {name}"}
        else:
            try:
                result = tool.invoke(args)
            except Exception as exc:  # noqa: BLE001 - surface tool errors to the model
                result = {"error": f"{type(exc).__name__}: {exc}"}
        if isinstance(result, dict) and result.get("hitl_pending"):
            hitl_pending = True
            hitl_payload = {
                "tool": name,
                "summary": result.get("ai_risk_summary", ""),
                "approval_routing": result.get("approval_routing")
                or result.get("approval_requests", []),
                "entity": result.get("po_number") or result.get("entity_id"),
            }
        tool_messages.append(
            ToolMessage(
                content=json.dumps(result, default=str),
                tool_call_id=call.get("id", name),
                name=name,
            )
        )
    return {
        "messages": tool_messages,
        "hitl_pending": hitl_pending,
        "hitl_payload": hitl_payload,
    }


def hitl_check_node(state):
    """Pause the graph when a tool result requires human approval."""
    if not state.get("hitl_pending"):
        return {}
    decision = interrupt(state.get("hitl_payload", {}))
    # Resumed: fold the human decision back into the conversation.
    decision_text = (
        decision.get("decision") if isinstance(decision, dict) else str(decision)
    )
    reason = decision.get("reason", "") if isinstance(decision, dict) else ""
    note = SystemMessage(
        content=(
            f"Human approval decision recorded: {decision_text}. {reason}".strip()
        )
    )
    return {"hitl_pending": False, "hitl_payload": {}, "messages": [note]}
