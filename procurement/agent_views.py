"""HTTP endpoints for the internal LangGraph orchestrator agent.

* ``POST /api/agent/chat/``     — Server-Sent Events stream of the agent's
  reasoning steps, policy citations, tool calls, and any HITL pause.
* ``POST /api/agent/approve/``  — resume a paused run with an approval decision.
* ``GET  /api/agent/sessions/`` — list known agent sessions for the caller.

Endpoints require an authenticated Django session and standard CSRF protection.
Session status is persisted in the ``AgentSession`` table so multiple web
workers (and the operator via Django Admin) see a consistent view.
"""

import json
import uuid

from django.db import transaction
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_http_methods

from .models import AgentSession


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _config(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def _require_auth(request):
    from django.conf import settings
    if getattr(settings, "REQUIRE_API_AUTH", False) and not request.user.is_authenticated:
        return JsonResponse(
            {"detail": "Authentication required."}, status=401
        )
    return None


def _touch_session(thread_id, user, *, status, message=None):
    defaults = {"user": user if user and user.is_authenticated else None, "status": status}
    if message is not None:
        defaults["last_message"] = message[:2000]
    with transaction.atomic():
        session, created = AgentSession.objects.get_or_create(
            session_id=thread_id, defaults=defaults
        )
        if not created:
            session.status = status
            if message is not None:
                session.last_message = message[:2000]
            if user and user.is_authenticated and session.user_id is None:
                session.user = user
            session.save(update_fields=["status", "last_message", "user", "last_activity"])
        return session


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


@require_http_methods(["POST"])
def agent_chat(request):
    auth_response = _require_auth(request)
    if auth_response is not None:
        return auth_response

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
    user = request.user
    _touch_session(thread_id, user, status=AgentSession.Status.RUNNING, message=message)

    def stream():
        yield _sse("session", {"session_id": thread_id})
        final_status = AgentSession.Status.IDLE
        emitted_hitl = False
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
                        final_status = AgentSession.Status.AWAITING_APPROVAL
                        _touch_session(thread_id, user, status=final_status)
                        yield _sse("hitl_pending", {"session_id": thread_id, "payload": payload})
                        emitted_hitl = True
                        continue
                    for event, data in _summarize_message(node, update):
                        yield _sse(event, data)
            # Fallback: some LangGraph paths surface the interrupt only via
            # get_state() rather than as a mid-stream __interrupt__ chunk.
            # Only fire when we did not already emit above, so the UI does not
            # render the same HITL card twice.
            state = graph.get_state(config)
            if state.next:
                final_status = AgentSession.Status.AWAITING_APPROVAL
                _touch_session(thread_id, user, status=final_status)
                if not emitted_hitl:
                    pending = []
                    for task in state.tasks:
                        for itr in getattr(task, "interrupts", []) or []:
                            pending.append(getattr(itr, "value", None))
                    if pending:
                        yield _sse("hitl_pending", {"session_id": thread_id, "payload": pending[0]})
            else:
                final_status = AgentSession.Status.IDLE
                _touch_session(thread_id, user, status=final_status)
            yield _sse("done", {"session_id": thread_id, "status": final_status})
        except Exception as exc:  # noqa: BLE001 - report streaming failures to the client
            _touch_session(thread_id, user, status=AgentSession.Status.FAILED)
            yield _sse("error", {"detail": f"{type(exc).__name__}: {exc}"})

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_http_methods(["POST"])
def agent_approve(request):
    auth_response = _require_auth(request)
    if auth_response is not None:
        return auth_response

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
        _touch_session(thread_id, request.user, status=AgentSession.Status.FAILED)
        return JsonResponse({"detail": f"{type(exc).__name__}: {exc}"}, status=500)

    state = graph.get_state(config)
    status = (
        AgentSession.Status.AWAITING_APPROVAL if state.next else AgentSession.Status.IDLE
    )
    _touch_session(thread_id, request.user, status=status)

    final_text = ""
    for msg in reversed(result.get("messages", [])):
        if getattr(msg, "type", "") == "ai" and isinstance(msg.content, str) and msg.content.strip():
            final_text = msg.content
            break

    # The model occasionally returns an empty closing message; synthesize a
    # confirmation from the persisted decision outcome so the user always sees
    # what happened.
    outcome = result.get("hitl_outcome") or {}
    if not final_text:
        if outcome and outcome.get("entity_id"):
            entity_label = (outcome.get("entity_type") or "entity").replace("_", " ").title()
            status_label = outcome.get("entity_status") or "updated"
            final_text = (
                f"Decision '{outcome.get('decision', decision)}' recorded. "
                f"{entity_label} {outcome['entity_id']} is now '{status_label}' "
                f"({outcome.get('approvals_updated', 0)} approval request(s) resolved)."
            )
        else:
            final_text = f"Decision '{decision}' recorded."

    return JsonResponse(
        {
            "session_id": thread_id,
            "status": status,
            "decision": decision,
            "final_message": final_text,
            "outcome": outcome,
        }
    )


@require_http_methods(["GET"])
def agent_sessions(request):
    auth_response = _require_auth(request)
    if auth_response is not None:
        return auth_response

    qs = AgentSession.objects.filter(user=request.user)
    return JsonResponse(
        {
            "count": qs.count(),
            "sessions": [
                {
                    "session_id": s.session_id,
                    "status": s.status,
                    "last_activity": s.last_activity.isoformat(),
                    "last_message": s.last_message,
                }
                for s in qs[:100]
            ],
        }
    )
