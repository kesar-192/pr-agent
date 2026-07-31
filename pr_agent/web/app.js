// pr_agent/web/app.js
//
// Maps directly onto the FastAPI backend in pr_agent/servers/prompting_server.py
// (endpoints/contracts unchanged from the previous version) and the data
// shapes in pr_agent/sessions/session.py:
//   ReviewSession { session_id, pr_url, pr_metadata, conversation_history,
//                    findings, current_finding_id, turn_count, ... }
//   ChatMessage    { role, content, timestamp, metadata }
//   Finding        { id, file, start_line, end_line, severity, category,
//                    explanation, question_for_developer, confidence, status }
//
// Endpoints used (unchanged):
//   GET    /api/v1/sessions                      -> { sessions: [...] }
//   POST   /api/v1/sessions            {pr_url}   -> { session_id, status }
//   GET    /api/v1/sessions/{id}                  -> full ReviewSession
//   GET    /api/v1/sessions/{id}/diff             -> { diff, annotations }
//   POST   /api/v1/sessions/{id}/messages {content}-> { stream_url }
//   GET    /streams/{id}                          -> SSE: token/finding_update/error/done
//   POST   /api/v1/sessions/{id}/cancel           -> { status }
//   DELETE /api/v1/sessions/{id}                  -> { status, summary }

const state = {
  sessionId: null,
  eventSource: null,
  findings: [],
  files: [],
};

const el = {
  // theme + sidebar
  themeToggleBtn: document.getElementById("theme-toggle-btn"),
  themeIcon: document.getElementById("theme-icon"),
  collapseSidebarBtn: document.getElementById("collapse-sidebar-btn"),
  appShell: document.getElementById("app-shell"),

  // top navbar
  newSessionBtn: document.getElementById("new-session-btn"),
  sessionPickerBtn: document.getElementById("session-picker-btn"),
  sessionPickerDropdown: document.getElementById("session-picker-dropdown"),
  sessionCountBadge: document.getElementById("session-count-badge"),
  sessionList: document.getElementById("session-list"),
  sessionListEmpty: document.getElementById("session-list-empty"),
  form: document.getElementById("pr-url-form"),
  urlInput: document.getElementById("pr-url-input"),
  startBtn: document.getElementById("start-review-btn"),
  liveDot: document.getElementById("live-dot"),
  sessionIdLabel: document.getElementById("session-id-label"),
  closeSessionBtn: document.getElementById("close-session-btn"),

  // left sidebar
  fileTree: document.getElementById("file-tree"),
  fileTreeEmpty: document.getElementById("file-tree-empty"),

  // center diff
  diffFileTitle: document.getElementById("diff-file-title"),
  diffStatusBadge: document.getElementById("diff-status-badge"),
  diffBody: document.getElementById("diff-body"),
  diffEmpty: document.getElementById("diff-empty"),

  // right chat
  chatLiveDot: document.getElementById("chat-live-dot"),
  chatLiveLabel: document.getElementById("chat-live-label"),
  findingsManifest: document.getElementById("findings-manifest"),
  chatMessages: document.getElementById("chat-messages"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  sendBtn: document.getElementById("send-btn"),
  cancelBtn: document.getElementById("cancel-btn"),

  // summary modal
  summaryModal: document.getElementById("summary-modal"),
  summaryBody: document.getElementById("summary-body"),
  closeSummaryBtn: document.getElementById("close-summary-btn"),

  // comment popover
  commentPopover: document.getElementById("comment-popover"),
  commentInput: document.getElementById("comment-input"),
  commentCancelBtn: document.getElementById("comment-cancel-btn"),
  commentSendBtn: document.getElementById("comment-send-btn"),
};

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// ------------------------------------------------------------------ //
// Theme toggle (dark default, persisted where possible)
// ------------------------------------------------------------------ //

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  el.themeIcon.textContent = theme === "light" ? "\u2600" : "\u263D"; // sun / moon
  try { localStorage.setItem("pr-agent-theme", theme); } catch { /* storage unavailable */ }
}

(function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("pr-agent-theme"); } catch { /* storage unavailable */ }
  const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  applyTheme(saved || (prefersLight ? "light" : "dark"));
})();

el.themeToggleBtn.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme");
  applyTheme(current === "light" ? "dark" : "light");
});

// ------------------------------------------------------------------ //
// Collapsible left sidebar
// ------------------------------------------------------------------ //

el.collapseSidebarBtn.addEventListener("click", () => {
  el.appShell.classList.toggle("sidebar-collapsed");
});

// ------------------------------------------------------------------ //
// Copy buttons with "Copied!" feedback — attached to every <pre>
// ------------------------------------------------------------------ //

function attachCopyButtons(container) {
  container.querySelectorAll("pre:not([data-copy-ready])").forEach((pre) => {
    pre.dataset.copyReady = "true";
    pre.style.position = "relative";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy-btn";
    btn.innerHTML = `<span class="label-default">Copy</span><span class="check">&#10003; Copied</span>`;
    btn.addEventListener("click", async () => {
      const text = pre.innerText.replace(/(Copy|.\u2713 Copied)$/, "").trim();
      try {
        await navigator.clipboard.writeText(text);
      } catch { /* clipboard unavailable — code remains selectable */ }
      btn.classList.add("copied");
      setTimeout(() => btn.classList.remove("copied"), 1500);
    });
    pre.appendChild(btn);
  });
}

function renderMarkdown(container, text) {
  container.innerHTML = marked.parse(text || "");
  attachCopyButtons(container);
  if (window.hljs) container.querySelectorAll("pre code").forEach((b) => hljs.highlightElement(b));
}

// ------------------------------------------------------------------ //
// Session picker dropdown
// ------------------------------------------------------------------ //

el.sessionPickerBtn.addEventListener("click", async () => {
  const expanded = el.sessionPickerBtn.getAttribute("aria-expanded") === "true";
  el.sessionPickerBtn.setAttribute("aria-expanded", String(!expanded));
  el.sessionPickerDropdown.hidden = expanded;
  if (!expanded) await refreshSessionList();
});

document.addEventListener("click", (e) => {
  if (!el.sessionPickerDropdown.hidden &&
      !el.sessionPickerDropdown.contains(e.target) &&
      !el.sessionPickerBtn.contains(e.target)) {
    el.sessionPickerDropdown.hidden = true;
    el.sessionPickerBtn.setAttribute("aria-expanded", "false");
  }
});

el.newSessionBtn.addEventListener("click", () => {
  resetToIdle();
  el.urlInput.focus();
});

async function refreshSessionList() {
  try {
    const res = await fetch("/api/v1/sessions");
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const { sessions } = await res.json();
    el.sessionCountBadge.textContent = sessions.length;
    el.sessionList.innerHTML = "";
    el.sessionListEmpty.hidden = sessions.length > 0;

    sessions.forEach((s) => {
      const li = document.createElement("li");
      li.className = "session-row";
      li.innerHTML = `
        <span class="dot run"></span>
        <div class="session-info">
          <div class="session-url">${escapeHtml(s.title || s.pr_url)}</div>
          <div class="session-meta">${escapeHtml(s.pr_url)} · turn ${s.turn_count} · ${s.open_finding_count} open</div>
        </div>
        <div class="session-actions">
          <button type="button" data-action="open">Open</button>
          <button type="button" class="danger" data-action="close">Close</button>
        </div>
      `;
      li.querySelector('[data-action="open"]').addEventListener("click", () => {
        openSession(s.session_id);
        el.sessionPickerDropdown.hidden = true;
        el.sessionPickerBtn.setAttribute("aria-expanded", "false");
      });
      li.querySelector('[data-action="close"]').addEventListener("click", (e) => {
        e.stopPropagation();
        endSession(s.session_id, li);
      });
      el.sessionList.appendChild(li);
    });
  } catch (err) {
    console.error("refreshSessionList failed:", err);
  }
}

async function openSession(sessionId) {
  state.sessionId = sessionId;
  el.sessionIdLabel.textContent = sessionId.slice(0, 8);
  el.liveDot.className = "dot run";
  el.diffStatusBadge.textContent = "active";
  el.diffStatusBadge.className = "status-badge active";
  el.chatInput.disabled = false;
  el.sendBtn.disabled = false;
  el.closeSessionBtn.disabled = false;
  setChatLive("connected", "run");

  el.chatMessages.innerHTML = "";
  el.findingsManifest.innerHTML = "";

  const session = await (await fetch(`/api/v1/sessions/${sessionId}`)).json();
  (session.conversation_history || []).forEach((m) => appendChatMessage(m.role, m.content));
  state.findings = session.findings || [];
  renderManifest();
  await renderDiff(sessionId);
}

async function endSession(sessionId, rowEl) {
  try {
    const res = await fetch(`/api/v1/sessions/${sessionId}`, { method: "DELETE" });
    const data = await res.json();

    if (rowEl) {
      rowEl.classList.add("leaving");
      setTimeout(() => rowEl.remove(), 250);
    }
    if (sessionId === state.sessionId) {
      showSummary(data.summary);
      resetToIdle();
    }
    setTimeout(refreshSessionList, 300);
  } catch (err) {
    console.error("endSession failed:", err);
  }
}

function resetToIdle() {
  state.sessionId = null;
  state.findings = [];
  el.sessionIdLabel.textContent = "no session";
  el.liveDot.className = "dot";
  el.diffStatusBadge.textContent = "no session";
  el.diffStatusBadge.className = "status-badge idle";
  el.diffFileTitle.textContent = "Diff";
  el.chatInput.disabled = true;
  el.sendBtn.disabled = true;
  el.closeSessionBtn.disabled = true;
  el.fileTree.innerHTML = "";
  el.fileTreeEmpty.hidden = false;
  el.diffBody.innerHTML = "";
  el.diffEmpty.hidden = false;
  el.findingsManifest.innerHTML = "";
  el.chatMessages.innerHTML = "";
  setChatLive("idle", "idle");
}

function showSummary(summaryText) {
  renderMarkdown(el.summaryBody, summaryText || "_No summary available._");
  el.summaryModal.hidden = false;
}
el.closeSummaryBtn.addEventListener("click", () => { el.summaryModal.hidden = true; });
el.summaryModal.addEventListener("click", (e) => {
  if (e.target === el.summaryModal) el.summaryModal.hidden = true;
});
el.closeSessionBtn.addEventListener("click", () => {
  if (state.sessionId) endSession(state.sessionId, null);
});

function setChatLive(labelText, kind) {
  el.chatLiveDot.className = `dot ${kind === "idle" ? "" : kind}`.trim();
  el.chatLiveLabel.textContent = labelText;
}

// ------------------------------------------------------------------ //
// Start a new review session
// ------------------------------------------------------------------ //

el.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const prUrl = el.urlInput.value.trim();
  if (!prUrl) return;

  el.startBtn.disabled = true;
  el.startBtn.querySelector(".btn-label").textContent = "Analyzing…";
  el.startBtn.querySelector(".btn-spinner").hidden = false;
  el.diffStatusBadge.textContent = "analyzing";
  el.diffStatusBadge.className = "status-badge active";

  try {
    const res = await fetch("/api/v1/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pr_url: prUrl }),
    });
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const { session_id } = await res.json();

    await openSession(session_id);
    el.urlInput.value = "";
    refreshSessionList();
  } catch (err) {
    console.error(err);
    el.diffStatusBadge.textContent = "error";
    el.diffStatusBadge.className = "status-badge error";
    appendChatMessage("system", `Failed to start review: ${err.message}`);
  } finally {
    el.startBtn.disabled = false;
    el.startBtn.querySelector(".btn-label").textContent = "Analyze";
    el.startBtn.querySelector(".btn-spinner").hidden = true;
  }
});

// ------------------------------------------------------------------ //
// Diff parsing / rendering with line numbers + file tree
// ------------------------------------------------------------------ //

function parseDiffIntoFiles(diffText) {
  const lines = diffText.split("\n");
  const files = [];
  let current = null;

  lines.forEach((line, i) => {
    const match = line.match(/^\+\+\+ b\/(.+)$/);
    if (match) {
      current = { path: match[1], lines: [] };
      files.push(current);
      return;
    }
    if (!current) {
      current = { path: "(diff header)", lines: [] };
      files.push(current);
    }
    current.lines.push({ raw: line, index: i + 1 });
  });

  return files;
}

async function renderDiff(sessionId) {
  const res = await fetch(`/api/v1/sessions/${sessionId}/diff`);
  if (!res.ok) return;
  const { diff, annotations } = await res.json();

  state.files = parseDiffIntoFiles(diff);
  renderFileTree(state.files, annotations);

  if (state.files.length) {
    selectFile(state.files[0], annotations);
  }
  el.diffEmpty.hidden = state.files.length > 0;
  el.fileTreeEmpty.hidden = state.files.length > 0;
}

function findingFor(lineNo, annotations) {
  return annotations.find((a) => lineNo >= a.start_line && lineNo <= a.end_line);
}

function renderFileTree(files, annotations) {
  el.fileTree.innerHTML = "";
  files.forEach((file) => {
    const count = annotations.filter((a) => a.file === file.path).length;
    const item = document.createElement("div");
    item.className = "file-tree-item";
    item.dataset.path = file.path;
    item.innerHTML = `<span class="file-icon">&#128196;</span><span>${escapeHtml(file.path)}</span>` +
      (count ? `<span class="finding-count-pill high">${count}</span>` : "");
    item.addEventListener("click", () => selectFile(file, annotations));
    el.fileTree.appendChild(item);
  });
}

function selectFile(file, annotations) {
  el.fileTree.querySelectorAll(".file-tree-item").forEach((n) => {
    n.classList.toggle("selected", n.dataset.path === file.path);
  });
  el.diffFileTitle.textContent = file.path;
  renderDiffTable(file.lines, annotations);
}

function renderDiffTable(lines, annotations) {
  el.diffBody.innerHTML = "";
  const frag = document.createDocumentFragment();

  lines.forEach((line) => {
    const finding = findingFor(line.index, annotations);
    const tr = document.createElement("tr");
    tr.className = "diff-row";
    if (line.raw.startsWith("+")) tr.classList.add("add");
    if (line.raw.startsWith("-")) tr.classList.add("remove");
    if (finding) {
      tr.classList.add("has-finding", finding.severity);
      tr.dataset.findingId = finding.finding_id;
    }

    const badge = finding
      ? `<span class="inline-severity-badge ${finding.severity}">${finding.severity}</span>`
      : "";

    tr.innerHTML = `
      <td class="line-no" data-line="${line.index}">${line.index}</td>
      <td class="line-content">
        ${escapeHtml(line.raw)}${badge}
        <button type="button" class="add-comment-btn" title="Discuss this line">+</button>
      </td>
    `;

    // Clicking a finding row jumps to (and highlights) its manifest card
    if (finding) {
      tr.addEventListener("click", (e) => {
        if (e.target.closest(".add-comment-btn") || e.target.closest(".line-no")) return;
        jumpToFinding(finding.finding_id);
      });
    }

    // Clicking the line number, or the inline "+" button, opens the comment popover
    const lineNoCell = tr.querySelector(".line-no");
    const commentBtn = tr.querySelector(".add-comment-btn");
    [lineNoCell, commentBtn].forEach((trigger) => {
      trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        openCommentPopover(trigger, line.index, finding);
      });
    });

    frag.appendChild(tr);
  });

  el.diffBody.appendChild(frag);
}

function jumpToFinding(findingId) {
  const card = el.findingsManifest.querySelector(`[data-finding-id="${findingId}"]`);
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("current");
    setTimeout(() => card.classList.remove("current"), 1500);
  }
}

// ------------------------------------------------------------------ //
// Inline "discuss this line" comment popover
// ------------------------------------------------------------------ //

let commentContext = null;

function openCommentPopover(anchorEl, lineNo, finding) {
  commentContext = { lineNo, finding };
  const rect = anchorEl.getBoundingClientRect();
  el.commentPopover.style.top = `${rect.bottom + window.scrollY + 6}px`;
  el.commentPopover.style.left = `${Math.min(rect.left + window.scrollX, window.innerWidth - 300)}px`;
  el.commentPopover.hidden = false;
  el.commentInput.value = finding
    ? `Regarding the ${finding.category || "finding"} at line ${lineNo} — `
    : `About line ${lineNo}: `;
  el.commentInput.focus();
}

function closeCommentPopover() {
  el.commentPopover.hidden = true;
  commentContext = null;
}

el.commentCancelBtn.addEventListener("click", closeCommentPopover);
document.addEventListener("click", (e) => {
  if (!el.commentPopover.hidden &&
      !el.commentPopover.contains(e.target) &&
      !e.target.closest(".line-no") &&
      !e.target.closest(".add-comment-btn")) {
    closeCommentPopover();
  }
});

el.commentSendBtn.addEventListener("click", () => {
  const text = el.commentInput.value.trim();
  if (!text) return;
  el.chatInput.value = text;
  closeCommentPopover();
  el.chatInput.focus();
});

// ------------------------------------------------------------------ //
// Findings manifest (right panel, above chat)
// ------------------------------------------------------------------ //

function renderManifest() {
  el.findingsManifest.innerHTML = "";
  state.findings.forEach((f) => {
    const div = document.createElement("div");
    div.className = "manifest-entry";
    div.dataset.findingId = f.id;
    div.innerHTML = `
      <div class="manifest-meta">
        <span class="inline-severity-badge ${f.severity}">${f.severity}</span>
        <span>${escapeHtml(f.file)}:${f.start_line}</span>
        <span class="manifest-status">${f.status}</span>
      </div>
      <div class="manifest-body">${escapeHtml(f.category)}: ${escapeHtml(f.explanation)}</div>
    `;
    div.addEventListener("click", () => {
      el.chatInput.value = `Regarding the ${f.category} finding in ${f.file}:${f.start_line} — `;
      el.chatInput.focus();
    });
    el.findingsManifest.appendChild(div);
  });
}

// ------------------------------------------------------------------ //
// Chat: send message, stream via SSE, render tokens live
// ------------------------------------------------------------------ //

function appendChatMessage(role, content) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  if (role === "system") {
    div.textContent = content;
  } else {
    renderMarkdown(div, content);
  }
  el.chatMessages.appendChild(div);
  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
  return div;
}

el.chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const content = el.chatInput.value.trim();
  if (!content || !state.sessionId) return;

  appendChatMessage("user", escapeHtml(content));
  el.chatInput.value = "";
  el.sendBtn.disabled = true;
  el.cancelBtn.hidden = false;
  setChatLive("streaming", "signal");

  const bubble = appendChatMessage("assistant", "");
  const cursor = document.createElement("span");
  cursor.className = "typing-cursor";
  bubble.appendChild(cursor);
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
      renderMarkdown(bubble, buffer);
      bubble.appendChild(cursor);
      el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
    });

    source.addEventListener("finding_update", (evt) => {
      const { finding_id, status } = JSON.parse(evt.data);
      const finding = state.findings.find((f) => f.id === finding_id);
      if (finding) finding.status = status;
      renderManifest();
    });

    source.addEventListener("error", (evt) => {
      try {
        const { message } = JSON.parse(evt.data);
        appendChatMessage("system", `Error: ${message}`);
      } catch {
        // native EventSource connection error, not a server-sent "error" event
      }
    });

    source.addEventListener("done", () => {
      cursor.remove();
      source.close();
      el.sendBtn.disabled = false;
      el.cancelBtn.hidden = true;
      setChatLive("connected", "run");
    });
  } catch (err) {
    console.error(err);
    cursor.remove();
    bubble.textContent = `Error: ${err.message}`;
    el.sendBtn.disabled = false;
    el.cancelBtn.hidden = true;
    setChatLive("error", "error");
  }
});

el.cancelBtn.addEventListener("click", async () => {
  if (!state.sessionId) return;
  await fetch(`/api/v1/sessions/${state.sessionId}/cancel`, { method: "POST" });
  if (state.eventSource) state.eventSource.close();
  el.sendBtn.disabled = false;
  el.cancelBtn.hidden = true;
  setChatLive("connected", "run");
});

// ------------------------------------------------------------------ //
// Init
// ------------------------------------------------------------------ //

resetToIdle();
refreshSessionList();