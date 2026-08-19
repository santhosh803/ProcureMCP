"""LangGraph StateGraph for the ProcureMCP orchestrator (RAG-in-the-loop).

Flow:
    START -> retrieve_policy_context -> reason
    reason  --(tool calls?)--> act        | --(final answer)--> END
    act -> hitl_check
    hitl_check --(approval pending)--> END (interrupt)
               --(otherwise)--------> reason

A MemorySaver checkpointer persists state so a run paused at a HITL gate can be
resumed with the approver's decision.
"""

from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from .nodes import (
    act_node,
    hitl_check_node,
    reason_node,
    retrieve_policy_context_node,
)


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    policy_context: list
    hitl_pending: bool
    hitl_payload: dict


def _route_after_reason(state):
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "act"
    return END


def _route_after_hitl(state):
    # If still pending, the interrupt already paused execution; otherwise loop.
    return "reason" if not state.get("hitl_pending") else END


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("retrieve_policy_context", retrieve_policy_context_node)
    graph.add_node("reason", reason_node)
    graph.add_node("act", act_node)
    graph.add_node("hitl_check", hitl_check_node)

    graph.add_edge(START, "retrieve_policy_context")
    graph.add_edge("retrieve_policy_context", "reason")
    graph.add_conditional_edges("reason", _route_after_reason, {"act": "act", END: END})
    graph.add_edge("act", "hitl_check")
    graph.add_conditional_edges("hitl_check", _route_after_hitl, {"reason": "reason", END: END})

    return graph.compile(checkpointer=MemorySaver())


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph
