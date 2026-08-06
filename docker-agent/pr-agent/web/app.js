// pr_agent/web/app.js
// Vanilla JS SPA logic: session lifecycle, message sending, SSE streaming,
// diff rendering with finding annotations, chat rendering.

const state = {
  sessionId: null,
  eventSource: null,
  findings: [],
};

const el = {
  form: document.getElementById("pr-url-form"),
  urlInput: document.getElementById("pr-url-input"),
  startBtn: document.getElementById("start-review-btn"),
  status: document.getElementById("session-status"),
  diffCode: document.getElementById("diff-code"),
  chatMessages: document.getElementById("chat-messages"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  sendBtn: document.getElementById("send-btn"),
  cancelBtn: document.getElementById("cancel-btn"),
};

function setStatus(text, cls) {
  el.status.textContent = text;
  el.status.className = `status ${cls}`;
}

function appendMessage(role, html) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = html;
  el.chatMessages.appendChild(div);
  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
  return div;
}

function renderFindingCard(finding) {
  return `
    <div class="finding-card" data-finding-id="${finding.finding_id || finding.id}">
      <div class="meta">${(finding.severity || "").toUpperCase()} · ${finding.file}:${finding.start_line}
        <span class="severity-badge ${finding.severity}">${finding.severity}</span>
      </div>
      <div>${finding.category || ""}: ${finding.explanation || ""}</div>
      ${finding.question_for_developer ? `<div class="meta">Q: ${finding.question_for_developer}</div>` : ""}
    </div>
  `;
}

async function renderDiff(sessionId) {
  const res = await fetch(`/api/v1/sessions/${sessionId}/diff`);
  if (!res.ok) return;
  const { diff, annotations } = await res.json();
  state.findings = annotations;

  const lines = diff.split("\n").map((line, i) => {
    const lineNo = i + 1;
    const finding = annotations.find(
      (a) => lineNo >= a.start_line && lineNo <= a.end_line
    );
    const cls = finding ? `diff-line has-finding ${finding.severity}` : "diff-line";
    const badge = finding
      ? `<span class="severity-badge ${finding.severity}" data-jump="${finding.finding_id}">${finding.severity}</span>`
      : "";
    return `<span class="${cls}" data-finding-id="${finding ? finding.finding_id : ""}">${escapeHtml(line)}${badge}</span>`;
  });

  el.diffCode.innerHTML = lines.join("\n");

  el.diffCode.querySelectorAll(".diff-line.has-finding").forEach((node) => {
    node.addEventListener("click", () => {
      const card = document.querySelector(
        `.finding-card[data-finding-id="${node.dataset.findingId}"]`
      );
      if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });
}

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// ------------------------------------------------------------------ //
// Start a new review session
// ------------------------------------------------------------------ //

el.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const prUrl = el.urlInput.value.trim();
  if (!prUrl) return;

  setStatus("analyzing", "analyzing");
  el.startBtn.disabled = true;

  try {
    const res = await fetch("/api/v1/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pr_url: prUrl }),
    });
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const { session_id } = await res.json();
    state.sessionId = session_id;

    setStatus("ready", "ready");
    el.chatInput.disabled = false;
    el.sendBtn.disabled = false;

    const session = await (await fetch(`/api/v1/sessions/${session_id}`)).json();
    (session.conversation_history || []).forEach((m) => {
      appendMessage(m.role, marked.parse(m.content));
    });
    session.findings.forEach((f) => appendMessage("assistant", renderFindingCard(f)));

    await renderDiff(session_id);
  } catch (err) {
    console.error(err);
    setStatus("error", "error");
    appendMessage("assistant", `Failed to start review: ${err.message}`);
  } finally {
    el.startBtn.disabled = false;
  }
});

// ------------------------------------------------------------------ //
// Send a chat message, open the SSE stream, render tokens as they arrive
// ------------------------------------------------------------------ //

el.chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const content = el.chatInput.value.trim();
  if (!content || !state.sessionId) return;

  appendMessage("user", escapeHtml(content));
  el.chatInput.value = "";
  el.sendBtn.disabled = true;
  el.cancelBtn.hidden = false;

  const assistantBubble = appendMessage("assistant", "");
  let buffer = "";

  try {
    const res = await fetch(`/api/v1/sessions/${state.sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const { stream_url } = await res.json();

    const source = new EventSource(stream_url);
    state.eventSource = source;

    source.addEventListener("token", (evt) => {
      const { content: token } = JSON.parse(evt.data);
      buffer += token;
      assistantBubble.innerHTML = marked.parse(buffer);
      el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
    });

    source.addEventListener("finding_update", (evt) => {
      const { finding_id, status } = JSON.parse(evt.data);
      const card = document.querySelector(`.finding-card[data-finding-id="${finding_id}"]`);
      if (card) card.querySelector(".meta").insertAdjacentHTML("beforeend", ` · ${status}`);
    });

    source.addEventListener("error", (evt) => {
      // Fired both for a server-sent "error" event and native connection errors
      try {
        const { message } = JSON.parse(evt.data);
        assistantBubble.innerHTML += `<br/><em>Error: ${message}</em>`;
      } catch {
        // native EventSource connection error - stream likely already closed server-side
      }
    });

    source.addEventListener("done", () => {
      source.close();
      el.sendBtn.disabled = false;
      el.cancelBtn.hidden = true;
    });
  } catch (err) {
    console.error(err);
    assistantBubble.innerHTML = `Error: ${err.message}`;
    el.sendBtn.disabled = false;
    el.cancelBtn.hidden = true;
  }
});

el.cancelBtn.addEventListener("click", async () => {
  if (!state.sessionId) return;
  await fetch(`/api/v1/sessions/${state.sessionId}/cancel`, { method: "POST" });
  if (state.eventSource) state.eventSource.close();
  el.sendBtn.disabled = false;
  el.cancelBtn.hidden = true;
});
