// Minimal SSE stream handler for the ProcureMCP agent chat.
// Uses fetch + a streaming reader so the message can be POSTed with a body
// (EventSource is GET-only); the server emits standard "event:/data:" SSE frames.

(function () {
  const messages = document.getElementById("messages");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  let sessionId = null;

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
    window.scrollTo(0, document.body.scrollHeight);
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
    const card = el("div", "rounded-lg bg-amber-50 border border-amber-300 p-3");
    card.appendChild(el("div", "text-xs font-semibold text-amber-800 mb-1", "&#9888; Human approval required"));
    if (payload && payload.summary) {
      card.appendChild(el("div", "text-xs text-amber-900 mb-2", payload.summary));
    }
    const btns = el("div", "flex gap-2 mt-1");
    const approve = el("button", "rounded-md bg-emerald-600 text-white text-xs px-3 py-1.5 hover:bg-emerald-700", "Approve");
    const reject = el("button", "rounded-md bg-red-600 text-white text-xs px-3 py-1.5 hover:bg-red-700", "Reject");
    approve.onclick = () => decide("approved", card);
    reject.onclick = () => decide("rejected", card);
    btns.appendChild(approve);
    btns.appendChild(reject);
    card.appendChild(btns);
    return card;
  }

  async function decide(decision, card) {
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    const resp = await fetch("/api/agent/approve/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, decision: decision }),
    });
    const data = await resp.json();
    const box = agentContainer();
    box.appendChild(
      el("div", "text-xs text-slate-600", "Decision recorded: <b>" + decision + "</b>. " + (data.final_message || ""))
    );
  }

  function handleEvent(box, event, data) {
    if (event === "session") {
      sessionId = data.session_id;
    } else if (event === "policy_citations" && data.citations && data.citations.length) {
      box.appendChild(policyCard(data.citations));
    } else if (event === "tool_call") {
      box.appendChild(toolCallCard(data.calls || []));
    } else if (event === "tool_result") {
      box.appendChild(toolResultCard(data.tool, data.result));
    } else if (event === "reasoning" && data.text) {
      box.appendChild(el("div", "text-sm text-slate-800 whitespace-pre-wrap", data.text));
    } else if (event === "hitl_pending") {
      box.appendChild(hitlCard(data.payload));
    } else if (event === "error") {
      box.appendChild(el("div", "text-xs text-red-600", "Error: " + (data.detail || "unknown")));
    }
  }

  async function send(message) {
    bubble("user", message);
    const box = agentContainer();
    box.appendChild(el("div", "text-xs text-slate-400", "Thinking&hellip;"));
    sendBtn.disabled = true;

    const resp = await fetch("/api/agent/chat/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message, session_id: sessionId }),
    });
    box.firstChild.remove(); // drop the "Thinking..." placeholder

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
    sendBtn.disabled = false;
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
      input.value = chip.textContent;
      input.focus();
    });
  });
})();
