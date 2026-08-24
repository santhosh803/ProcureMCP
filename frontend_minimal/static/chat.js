// Minimal SSE stream handler for the ProcureMCP agent chat.
// Uses fetch + a streaming reader so the message can be POSTed with a body
// (EventSource is GET-only); the server emits standard "event:/data:" SSE frames.

(function () {
  const messages = document.getElementById("messages");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const promptChips = document.getElementById("prompt-chips");
  let sessionId = null;

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function jsonHeaders() {
    return {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken(),
    };
  }

  function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html !== undefined) e.innerHTML = html;
    return e;
  }

  function bubble(role, node) {
    const wrap = el("div", role === "user" ? "flex justify-end" : "flex justify-start");
    const card = el(
      "div",
      role === "user"
        ? "max-w-[80%] rounded-2xl bg-indigo-600 text-white px-4 py-2 text-sm"
        : "max-w-[85%] rounded-2xl bg-white border border-slate-200 px-4 py-3 text-sm text-slate-800"
    );
    if (typeof node === "string") card.textContent = node;
    else card.appendChild(node);
    wrap.appendChild(card);
    messages.appendChild(wrap);
    messages.scrollTop = messages.scrollHeight;
    return card;
  }

  function agentContainer() {
    const box = el("div", "space-y-2");
    bubble("agent", box);
    return box;
  }

  function policyCard(citations) {
    const card = el("div", "rounded-lg bg-emerald-50 border border-emerald-200 p-3");
    card.appendChild(el("div", "text-xs font-semibold text-emerald-700 mb-1", "&#128220; Policy context retrieved"));
    citations.forEach((c) => {
      const row = el("div", "text-xs text-emerald-900 mb-1");
      row.innerHTML =
        '<span class="font-medium">' + c.title + "</span> " +
        '<span class="text-emerald-600">[' + c.policy_type + " &middot; sim " +
        (c.similarity_score != null ? c.similarity_score.toFixed(2) : "?") + "]</span>";
      card.appendChild(row);
    });
    return card;
  }

  function toolCallCard(calls) {
    const card = el("div", "rounded-lg bg-slate-50 border border-slate-200 p-3");
    calls.forEach((c) => {
      const head = el("div", "text-xs font-semibold text-indigo-700", "&#128295; " + c.name);
      card.appendChild(head);
      const args = el("pre", "text-[11px] text-slate-500 mt-1 overflow-x-auto");
      args.textContent = JSON.stringify(c.args || {}, null, 0);
      card.appendChild(args);
    });
    return card;
  }

  function toolResultCard(tool, result) {
    const wrap = el("details", "rounded-lg bg-slate-50 border border-slate-200 p-3");
    const sum = el("summary", "text-xs font-medium text-slate-600 cursor-pointer", "Result: " + tool);
    wrap.appendChild(sum);
    const pre = el("pre", "text-[11px] text-slate-600 mt-2 overflow-x-auto");
    pre.textContent = JSON.stringify(result, null, 2);
    wrap.appendChild(pre);
    return wrap;
  }

  function hitlCard(payload) {
    const card = el("div", "rounded-lg bg-amber-50 border border-amber-300 p-3 transition-colors");
    card.dataset.state = "pending";
    card.appendChild(el("div", "text-xs font-semibold text-amber-800 mb-1 hitl-heading", "&#9888; Human approval required"));
    if (payload && payload.summary) {
      card.appendChild(el("div", "text-xs text-amber-900 mb-2", payload.summary));
    }
    const btns = el("div", "flex gap-2 mt-1 hitl-actions");
    const approve = el("button", "rounded-md bg-emerald-600 text-white text-xs px-3 py-1.5 hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed", "Approve");
    const reject = el("button", "rounded-md bg-red-600 text-white text-xs px-3 py-1.5 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed", "Reject");
    approve.onclick = () => decide("approved", card);
    reject.onclick = () => decide("rejected", card);
    btns.appendChild(approve);
    btns.appendChild(reject);
    card.appendChild(btns);
    return card;
  }

  function applyDecidedStyle(card, decision) {
    // Swap the amber "pending" palette for a decision-coloured one so the card
    // itself shows what was chosen — no ambiguity from a nearby button state.
    const isApproved = decision === "approved";
    card.classList.remove("bg-amber-50", "border-amber-300");
    card.classList.add(
      isApproved ? "bg-emerald-50" : "bg-red-50",
      isApproved ? "border-emerald-300" : "border-red-300"
    );
    card.dataset.state = decision;

    const heading = card.querySelector(".hitl-heading");
    if (heading) {
      heading.classList.remove("text-amber-800");
      heading.classList.add(isApproved ? "text-emerald-800" : "text-red-800");
      heading.innerHTML = isApproved
        ? "&#10003; Approved by you"
        : "&#10007; Rejected by you";
    }
  }

  async function decide(decision, card) {
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    let data = {};
    try {
      const resp = await fetch("/api/agent/approve/", {
        method: "POST",
        credentials: "same-origin",
        headers: jsonHeaders(),
        body: JSON.stringify({ session_id: sessionId, decision: decision }),
      });
      data = await resp.json();
    } catch (e) {
      data = { detail: String(e) };
    }

    // Rewrite the HITL card in place to reflect the decision.
    applyDecidedStyle(card, decision);
    const actions = card.querySelector(".hitl-actions");
    if (actions) actions.remove();
    const outcome = data.outcome || {};
    const parts = [];
    if (outcome.entity_id) {
      const label = (outcome.entity_type || "entity").replace(/_/g, " ");
      parts.push(
        "<b>" + label + " " + outcome.entity_id + "</b>" +
          (outcome.entity_status ? " &rarr; <b>" + outcome.entity_status + "</b>" : "")
      );
      if (typeof outcome.approvals_updated === "number") {
        parts.push(outcome.approvals_updated + " approval request(s) resolved");
      }
    }
    if (data.detail) parts.push("<span class='text-red-700'>" + data.detail + "</span>");
    const summaryText = parts.length ? parts.join(" &middot; ") : "Decision recorded.";
    const isApproved = decision === "approved";
    card.appendChild(
      el(
        "div",
        "text-xs mt-2 " + (isApproved ? "text-emerald-900" : "text-red-900"),
        summaryText
      )
    );

    // Agent's closing message goes in its own bubble below, as before.
    if (data.final_message) {
      const box = agentContainer();
      box.appendChild(el("div", "text-xs text-slate-600", data.final_message));
    }
  }

  function thinkingIndicator(text = "Searching policies & reasoning…") {
    const card = el(
      "div",
      "flex items-center gap-2.5 text-xs text-slate-500 py-2 px-3 rounded-lg bg-slate-50 border border-slate-200 agent-thinking-indicator"
    );
    card.innerHTML =
      '<span class="inline-flex items-center gap-1">' +
      '<span class="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" style="animation-delay: 0ms"></span>' +
      '<span class="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" style="animation-delay: 150ms"></span>' +
      '<span class="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" style="animation-delay: 300ms"></span>' +
      '</span>' +
      '<span class="thinking-label font-medium text-slate-600">' + text + '</span>';
    return card;
  }

  function setThinkingText(box, text) {
    let indicator = box.querySelector(".agent-thinking-indicator");
    if (!indicator) {
      indicator = thinkingIndicator(text);
      box.appendChild(indicator);
    } else {
      const label = indicator.querySelector(".thinking-label");
      if (label) label.textContent = text;
      box.appendChild(indicator);
    }
    messages.scrollTop = messages.scrollHeight;
  }

  function removeThinking(box) {
    const indicator = box.querySelector(".agent-thinking-indicator");
    if (indicator) indicator.remove();
  }

  function handleEvent(box, event, data) {
    if (event === "session") {
      sessionId = data.session_id;
    } else if (event === "policy_citations" && data.citations && data.citations.length) {
      removeThinking(box);
      box.appendChild(policyCard(data.citations));
      setThinkingText(box, "Evaluating policy rules & planning tool calls…");
    } else if (event === "tool_call") {
      removeThinking(box);
      box.appendChild(toolCallCard(data.calls || []));
      setThinkingText(box, "Executing procurement tools…");
    } else if (event === "tool_result") {
      removeThinking(box);
      box.appendChild(toolResultCard(data.tool, data.result));
      setThinkingText(box, "Synthesizing result & formulating response…");
    } else if (event === "reasoning" && data.text) {
      removeThinking(box);
      box.appendChild(el("div", "text-sm text-slate-800 whitespace-pre-wrap", data.text));
    } else if (event === "hitl_pending") {
      removeThinking(box);
      box.appendChild(hitlCard(data.payload));
    } else if (event === "error") {
      removeThinking(box);
      box.appendChild(el("div", "text-xs text-red-600", "Error: " + (data.detail || "unknown")));
    } else if (event === "done") {
      removeThinking(box);
    }
    messages.scrollTop = messages.scrollHeight;
  }

  async function send(message) {
    // Hide the quick-prompt chips once the conversation begins.
    // Inline style beats Tailwind's `.flex` (which would otherwise re-show it).
    if (promptChips) promptChips.style.display = "none";
    bubble("user", message);
    const box = agentContainer();
    setThinkingText(box, "Analyzing request & retrieving policies…");
    sendBtn.disabled = true;
    sendBtn.innerHTML = '<span class="inline-flex items-center gap-1.5"><span class="w-1.5 h-1.5 rounded-full bg-white animate-ping"></span> Thinking…</span>';

    try {
      const resp = await fetch("/api/agent/chat/", {
        method: "POST",
        credentials: "same-origin",
        headers: jsonHeaders(),
        body: JSON.stringify({ message: message, session_id: sessionId }),
      });

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop();
        for (const frame of frames) {
          let event = "message";
          let dataStr = "";
          frame.split("\n").forEach((line) => {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
          });
          if (!dataStr) continue;
          try {
            handleEvent(box, event, JSON.parse(dataStr));
          } catch (e) {
            /* ignore malformed frame */
          }
        }
      }
    } catch (err) {
      removeThinking(box);
      box.appendChild(el("div", "text-xs text-red-600", "Connection error: " + err.message));
    } finally {
      removeThinking(box);
      sendBtn.disabled = false;
      sendBtn.textContent = "Send";
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    send(msg);
  });

  document.querySelectorAll(".prompt-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      input.value = chip.textContent.trim();
      input.focus();
    });
  });
})();
