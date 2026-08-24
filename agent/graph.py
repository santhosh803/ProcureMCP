"""LangGraph StateGraph for the ProcureMCP orchestrator (RAG-in-the-loop).

Flow:
    START -> retrieve_policy_context -> reason
    reason  --(tool calls?)--> act        | --(final answer)--> END
    act -> hitl_check
    hitl_check --(approval pending)--> END (interrupt)
               --(otherwise)--------> reason

The graph is compiled with a checkpointer so a run paused at a HITL gate can be
resumed with the approver's decision. In production the checkpointer is a
PostgresSaver backed by the shared Neon database, which makes sessions durable
across process restarts and shareable across multiple web/worker processes. In
the test suite (and any environment without ``DATABASE_URL``) it falls back to
an in-process MemorySaver so tests remain self-contained.
"""

import os
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
    hitl_outcome: dict


def _route_after_reason(state):
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "act"
    return END


def _route_after_hitl(state):
    return "reason" if not state.get("hitl_pending") else END


_pg_pool = None
_pg_checkpointer = None


def _get_postgres_checkpointer():
    """Lazily open a shared connection pool and return a PostgresSaver on it.

    Runs ``PostgresSaver.setup()`` on first use so the checkpoint tables exist
    in the target database.
    """
    global _pg_pool, _pg_checkpointer
    if _pg_checkpointer is not None:
        return _pg_checkpointer

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg_pool import ConnectionPool

    conninfo = os.environ.get("DATABASE_URL")
    if not conninfo:
        return None

    _pg_pool = ConnectionPool(
        conninfo=conninfo,
        max_size=int(os.environ.get("AGENT_CHECKPOINT_POOL_SIZE", "5")),
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=True,
    )
    _pg_checkpointer = PostgresSaver(_pg_pool)
    _pg_checkpointer.setup()
    return _pg_checkpointer


def _default_checkpointer():
    """Return the checkpointer appropriate for the current environment."""
    from django.conf import settings

    backend = str(getattr(settings, "LANGGRAPH_CHECKPOINT_BACKEND", "postgres")).strip().lower()
    if backend == "memory" or getattr(settings, "TESTING", False) or not os.environ.get("DATABASE_URL"):
        return MemorySaver()
    try:
        cp = _get_postgres_checkpointer()
        return cp or MemorySaver()
    except Exception:  # noqa: BLE001 - never let checkpointer setup crash boot
        return MemorySaver()


def build_graph(checkpointer=None):
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

    return graph.compile(checkpointer=checkpointer or _default_checkpointer())


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph
