"""HTTP endpoints for the internal LangGraph orchestrator agent.

* ``POST /api/agent/chat/``     — Server-Sent Events stream of the agent's
  reasoning steps, policy citations, tool calls, and any HITL pause.
* ``POST /api/agent/approve/``  — resume a paused run with an approval decision.
* ``GET  /api/agent/sessions/`` — list known agent sessions.
"""

import json
import uuid

from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

# Simple in-process session registry (single-worker dev/demo).
_SESSIONS = {}


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _config(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def _summarize_message(node, update):
    """Translate a graph state update into SSE-friendly events."""
    events = []
    if node == "retrieve_policy_context":
        citations = update.get("policy_context") or []
        events.append(("policy_citations", {"citations": citations}))
    if "messages" in (update or {}):
        for msg in update["messages"]:
            mtype = getattr(msg, "type", "")
            if mtype == "ai":
                tool_calls = getattr(msg, "tool_calls", None) or []
                if tool_calls:
                    events.append((
                        "tool_call",
                        {"calls": [{"name": c["name"], "args": c.get("args", {})} for c in tool_calls]},
                    ))
                if isinstance(msg.content, str) and msg.content.strip():
                    events.append(("reasoning", {"text": msg.content}))
            elif mtype == "tool":
                try:
                    payload = json.loads(msg.content)
                except (json.JSONDecodeError, TypeError):
                    payload = {"raw": str(msg.content)}
                events.append(("tool_result", {"tool": getattr(msg, "name", ""), "result": payload}))
    return events


@csrf_exempt
@require_http_methods(["POST"])
def agent_chat(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON body."}, status=400)

    message = (body.get("message") or "").strip()
    if not message:
        return JsonResponse({"detail": "A non-empty 'message' is required."}, status=400)
    thread_id = body.get("session_id") or str(uuid.uuid4())

    from langchain_core.messages import HumanMessage

    from agent.graph import get_graph

    graph = get_graph()
    config = _config(thread_id)
    _SESSIONS[thread_id] = {"last_activity": timezone.now().isoformat(), "status": "running"}

    def stream():
        yield _sse("session", {"session_id": thread_id})
        try:
            for chunk in graph.stream(
                {"messages": [HumanMessage(content=message)]},
                config,
                stream_mode="updates",
            ):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        interrupts = update if isinstance(update, (list, tuple)) else [update]
                        payload = getattr(interrupts[0], "value", interrupts[0])
                        _SESSIONS[thread_id]["status"] = "awaiting_approval"
                        yield _sse("hitl_pending", {"session_id": thread_id, "payload": payload})
                        continue
                    for event, data in _summarize_message(node, update):
                        yield _sse(event, data)
            # Detect a pause captured outside the update stream.
            state = graph.get_state(config)
            if state.next:
                _SESSIONS[thread_id]["status"] = "awaiting_approval"
                pending = []
                for task in state.tasks:
                    for itr in getattr(task, "interrupts", []) or []:
                        pending.append(getattr(itr, "value", None))
                if pending:
                    yield _sse("hitl_pending", {"session_id": thread_id, "payload": pending[0]})
            else:
                _SESSIONS[thread_id]["status"] = "idle"
            yield _sse("done", {"session_id": thread_id, "status": _SESSIONS[thread_id]["status"]})
        except Exception as exc:  # noqa: BLE001 - report streaming failures to the client
            yield _sse("error", {"detail": f"{type(exc).__name__}: {exc}"})

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@csrf_exempt
@require_http_methods(["POST"])
def agent_approve(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON body."}, status=400)

    thread_id = body.get("session_id")
    decision = body.get("decision")
    if not thread_id or not decision:
        return JsonResponse(
            {"detail": "'session_id' and 'decision' are required."}, status=400
        )

    from langgraph.types import Command

    from agent.graph import get_graph

    graph = get_graph()
    config = _config(thread_id)
    resume_value = {"decision": decision, "reason": body.get("reason", "")}

    try:
        result = graph.invoke(Command(resume=resume_value), config)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"detail": f"{type(exc).__name__}: {exc}"}, status=500)

    state = graph.get_state(config)
    status = "awaiting_approval" if state.next else "idle"
    _SESSIONS.setdefault(thread_id, {})
    _SESSIONS[thread_id].update({"last_activity": timezone.now().isoformat(), "status": status})

    final_text = ""
    for msg in reversed(result.get("messages", [])):
        if getattr(msg, "type", "") == "ai" and isinstance(msg.content, str) and msg.content.strip():
            final_text = msg.content
            break
    return JsonResponse(
        {"session_id": thread_id, "status": status, "decision": decision, "final_message": final_text}
    )


@require_http_methods(["GET"])
def agent_sessions(request):
    return JsonResponse(
        {
            "count": len(_SESSIONS),
            "sessions": [
                {"session_id": sid, **info} for sid, info in _SESSIONS.items()
            ],
        }
    )
