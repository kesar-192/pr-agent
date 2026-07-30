"use client";

import { useState, useRef, useEffect } from "react";

const PHASE = { IDLE: 1, LOADING: 2, READY: 3 };

const SEVERITY_COLORS = {
  critical: { dot: "bg-red-500", label: "text-red-400" },
  high: { dot: "bg-orange-500", label: "text-orange-400" },
  medium: { dot: "bg-yellow-500", label: "text-yellow-400" },
  low: { dot: "bg-blue-500", label: "text-blue-400" },
};

const EXAMPLE_PRS = [
  { label: "The-PR-Agent/pr-agent#2574", url: "https://github.com/The-PR-Agent/pr-agent/pull/2574" },
  { label: "facebook/react#12345", url: "https://github.com/facebook/react/pull/12345" },
];

export default function PRAgentUI() {
  const [phase, setPhase] = useState(PHASE.IDLE);
  const [prUrl, setPrUrl] = useState("");
  const [error, setError] = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [findings, setFindings] = useState([]);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [selectedFinding, setSelectedFinding] = useState(null);
  const eventSourceRef = useRef(null);
  const chatEndRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (phase === PHASE.READY && !streaming) {
      inputRef.current?.focus();
    }
  }, [phase, streaming]);

  async function startReview() {
    if (!prUrl.trim()) return;
    setPhase(PHASE.LOADING);
    setError(null);
    setFindings([]);
    setMessages([]);
    setSessionId(null);
    setSelectedFinding(null);

    try {
      const res = await fetch("/api/v1/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pr_url: prUrl }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || `Request failed (${res.status})`);
      }
      const { session_id } = await res.json();
      setSessionId(session_id);

      const [sessionRes, diffRes] = await Promise.all([
        fetch(`/api/v1/sessions/${session_id}`),
        fetch(`/api/v1/sessions/${session_id}/diff`),
      ]);
      const session = await sessionRes.json();
      const diffData = await diffRes.json();

      setFindings(session.findings || []);
      setMessages(session.conversation_history || []);
      setSelectedFinding(session.current_finding_id);
      setPhase(PHASE.READY);
    } catch (err) {
      setError(err.message);
      setPhase(PHASE.IDLE);
    }
  }

  async function sendMessage() {
    if (!input.trim() || streaming || !sessionId) return;
    const text = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setStreaming(true);

    try {
      const res = await fetch(`/api/v1/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text }),
      });
      if (!res.ok) throw new Error("Failed to send message");
      const { stream_url } = await res.json();

      const es = new EventSource(stream_url);
      eventSourceRef.current = es;

      es.addEventListener("token", (e) => {
        const data = JSON.parse(e.data);
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.role === "assistant" && last._streaming) {
            return [...prev.slice(0, -1), { role: "assistant", content: data.content, _streaming: true }];
          }
          return [...prev, { role: "assistant", content: data.content, _streaming: true }];
        });
      });

      es.addEventListener("finding_update", (e) => {
        const data = JSON.parse(e.data);
        setFindings((prev) =>
          prev.map((f) => (f.id === data.finding_id ? { ...f, status: data.status } : f))
        );
      });

      es.addEventListener("done", () => {
        es.close();
        eventSourceRef.current = null;
        setStreaming(false);
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?._streaming) {
            return [...prev.slice(0, -1), { role: "assistant", content: last.content }];
          }
          return prev;
        });
        fetch(`/api/v1/sessions/${sessionId}`)
          .then((r) => r.json())
          .then((s) => {
            setFindings(s.findings || []);
            setSelectedFinding(s.current_finding_id);
          })
          .catch(() => {});
      });

      es.addEventListener("error", (e) => {
        const data = JSON.parse(e.data);
        setMessages((prev) => [...prev, { role: "system", content: `Error: ${data.message}` }]);
        es.close();
        eventSourceRef.current = null;
        setStreaming(false);
      });
    } catch (err) {
      setMessages((prev) => [...prev, { role: "system", content: `Error: ${err.message}` }]);
      setStreaming(false);
    }
  }

  async function closeSession() {
    if (!sessionId) return;
    try {
      await fetch(`/api/v1/sessions/${sessionId}`, { method: "DELETE" });
    } catch {}
    cleanup();
  }

  function cancel() {
    if (!sessionId || !streaming) return;
    fetch(`/api/v1/sessions/${sessionId}/cancel`, { method: "POST" }).catch(() => {});
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setStreaming(false);
  }

  function cleanup() {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setPhase(PHASE.IDLE);
    setPrUrl("");
    setSessionId(null);
    setFindings([]);
    setMessages([]);
    setError(null);
    setStreaming(false);
    setSelectedFinding(null);
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (phase === PHASE.IDLE) startReview();
      else if (phase === PHASE.READY) sendMessage();
    }
  }

  function activeFindings() {
    return findings.filter((f) => f.status === "open" || f.status === "discussed");
  }

  if (phase === PHASE.LOADING) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-yard-950">
        <div className="panel w-full max-w-lg p-8 text-center">
          <div className="flex items-center justify-center gap-3">
            <span className="relative flex h-3 w-3">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-haul opacity-75" />
              <span className="relative inline-flex h-3 w-3 rounded-full bg-haul" />
            </span>
            <span className="font-mono text-sm text-ink-muted">
              Analyzing PR diff & running initial review...
            </span>
          </div>
          {prUrl && (
            <p className="mt-4 truncate font-mono text-xs text-ink-faint">{prUrl}</p>
          )}
        </div>
      </div>
    );
  }

  if (phase === PHASE.READY) {
    const active = activeFindings();
    return (
      <div className="flex min-h-screen flex-col bg-yard-950">
        {/* ── Top bar ── */}
        <header className="sticky top-0 z-50 border-b border-yard-700/60 bg-yard-950/80 backdrop-blur-md">
          <div className="container-px mx-auto flex h-14 max-w-6xl items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="flex h-6 w-6 items-center justify-center rounded-md bg-haul/15 text-haul shadow-glow-blue">
                <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 fill-current">
                  <rect x="3" y="3" width="8" height="8" rx="1" />
                  <rect x="13" y="3" width="8" height="8" rx="1" />
                  <rect x="3" y="13" width="8" height="8" rx="1" />
                </svg>
              </span>
              <span className="font-display text-sm font-semibold text-ink">PR-Agent</span>
              <span className="hidden font-mono text-xs text-ink-faint md:inline-block">
                {sessionId?.slice(0, 8)}&hellip;
              </span>
            </div>
            <div className="flex items-center gap-3">
              <button onClick={cleanup} className="rounded-lg border border-yard-700 px-3 py-1.5 font-mono text-xs text-ink-muted transition hover:border-haul hover:text-ink">
                New Review
              </button>
              <button onClick={closeSession} className="rounded-lg border border-yard-700 px-3 py-1.5 font-mono text-xs text-ink-muted transition hover:border-red-400 hover:text-red-400">
                Close
              </button>
            </div>
          </div>
        </header>

        {/* ── Main content: findings (left) + chat (right) ── */}
        <div className="container-px mx-auto flex w-full max-w-6xl flex-1 gap-0 lg:gap-6">
          {/* ── Findings panel ── */}
          <aside className="hidden w-72 shrink-0 border-r border-yard-700/60 pt-4 lg:block lg:border-r-0">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-display text-sm font-semibold text-ink">Findings</h2>
              <span className="rounded-full bg-yard-800 px-2 py-0.5 font-mono text-xs text-ink-faint">
                {findings.length}
              </span>
            </div>

            <div className="space-y-2">
              {findings.map((f) => {
                const colors = SEVERITY_COLORS[f.severity] || SEVERITY_COLORS.low;
                const isSelected = selectedFinding === f.id;
                return (
                  <button
                    key={f.id}
                    onClick={() => setSelectedFinding(f.id === selectedFinding ? null : f.id)}
                    className={`w-full rounded-lg border px-3 py-2.5 text-left transition ${
                      isSelected
                        ? "border-haul/50 bg-haul/5"
                        : "border-yard-700 bg-yard-900/50 hover:border-yard-600"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className={`h-2 w-2 shrink-0 rounded-full ${colors.dot}`} />
                      <span className={`truncate font-mono text-xs ${colors.label}`}>
                        {f.severity}
                      </span>
                      {f.status !== "open" && (
                        <span className="ml-auto rounded bg-yard-800 px-1.5 py-0.5 font-mono text-[10px] text-ink-faint">
                          {f.status}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 truncate font-mono text-xs text-ink-muted">
                      {f.file}:{f.start_line}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-ink-faint">{f.category}</p>
                  </button>
                );
              })}
              {findings.length === 0 && (
                <p className="py-8 text-center font-mono text-xs text-ink-faint">No findings yet</p>
              )}
            </div>

            {/* Selected finding detail */}
            {selectedFinding && (() => {
              const f = findings.find((x) => x.id === selectedFinding);
              if (!f) return null;
              return (
                <div className="mt-4 rounded-lg border border-yard-700 bg-yard-900/70 p-3">
                  <p className="mb-1 font-mono text-xs text-ink-muted">Explanation</p>
                  <p className="text-xs leading-relaxed text-ink">{f.explanation}</p>
                  {f.question_for_developer && (
                    <>
                      <p className="mb-1 mt-3 font-mono text-xs text-signal">Question for you</p>
                      <p className="text-xs leading-relaxed text-ink-muted">{f.question_for_developer}</p>
                    </>
                  )}
                  {f.resolution_note && (
                    <>
                      <p className="mb-1 mt-3 font-mono text-xs text-run">Resolution note</p>
                      <p className="text-xs leading-relaxed text-run">{f.resolution_note}</p>
                    </>
                  )}
                </div>
              );
            })()}
          </aside>

          {/* ── Chat panel ── */}
          <div className="flex flex-1 flex-col pt-4">
            {/* Messages */}
            <div className="flex-1 space-y-4 overflow-y-auto pb-4">
              {messages.map((m, i) => {
                if (m.role === "system") {
                  return (
                    <div key={i} className="rounded-lg border border-signal/30 bg-signal/5 px-4 py-3">
                      <p className="font-mono text-xs text-signal">{m.content}</p>
                    </div>
                  );
                }
                const isUser = m.role === "user";
                return (
                  <div key={i} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
                    <div
                      className={`max-w-[80%] rounded-lg px-4 py-3 ${
                        isUser
                          ? "bg-haul/15 text-ink"
                          : m._streaming
                            ? "border border-yard-700 bg-yard-900/70"
                            : "border border-yard-700 bg-yard-900/70"
                      }`}
                    >
                      <p className="font-mono text-xs text-ink-faint">
                        {isUser ? "You" : "Assistant"}
                      </p>
                      <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">
                        {m.content}
                        {m._streaming && <span className="ml-0.5 animate-blink">▊</span>}
                      </p>
                    </div>
                  </div>
                );
              })}
              {messages.length === 0 && (
                <div className="flex items-center justify-center py-16">
                  <div className="text-center">
                    <p className="font-mono text-sm text-ink-muted">Review complete.</p>
                    <p className="mt-2 font-mono text-xs text-ink-faint">
                      Ask a question about the findings above.
                    </p>
                  </div>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            {/* ── Input bar ── */}
            <div className="sticky bottom-0 border-t border-yard-700/60 bg-yard-950 py-3">
              <div className="flex items-center gap-3">
                <input
                  ref={inputRef}
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask a question about the PR..."
                  disabled={streaming}
                  className="flex-1 rounded-lg border border-yard-700 bg-yard-900 px-4 py-2.5 font-mono text-sm text-ink outline-none transition placeholder:text-ink-faint focus:border-haul disabled:opacity-50"
                />
                {streaming ? (
                  <button
                    onClick={cancel}
                    className="rounded-lg border border-red-400/50 px-4 py-2.5 font-mono text-xs text-red-400 transition hover:bg-red-400/10"
                  >
                    Cancel
                  </button>
                ) : (
                  <button
                    onClick={sendMessage}
                    disabled={!input.trim()}
                    className="rounded-lg bg-haul px-4 py-2.5 font-mono text-xs font-medium text-white shadow-glow-blue transition hover:bg-haul-dim disabled:opacity-50"
                  >
                    Send
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  /* ═══════════════════════════════════════════════════════════════
     IDLE phase — PR URL input
     ═══════════════════════════════════════════════════════════════ */
  return (
    <div className="flex min-h-screen flex-col bg-yard-950">
      <header className="sticky top-0 z-50 border-b border-yard-700/60 bg-yard-950/80 backdrop-blur-md">
        <nav className="container-px mx-auto flex h-16 max-w-6xl items-center justify-between">
          <a href="#" className="flex items-center gap-2 font-display text-lg font-semibold tracking-tight text-ink">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-haul/15 text-haul shadow-glow-blue">
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 fill-current">
                <rect x="3" y="3" width="8" height="8" rx="1" />
                <rect x="13" y="3" width="8" height="8" rx="1" />
                <rect x="3" y="13" width="8" height="8" rx="1" />
              </svg>
            </span>
            PR-Agent
          </a>
        </nav>
      </header>

      <main className="flex flex-1 items-center justify-center">
        <div className="w-full max-w-xl px-6">
          {/* Hero */}
          <div className="text-center">
            <p className="eyebrow mb-4">Interactive Code Review</p>
            <h1 className="font-display text-4xl font-semibold leading-[1.08] tracking-tight text-ink sm:text-5xl">
              Review pull requests
              <br />
              with <span className="text-haul">AI-powered</span> discussion.
            </h1>
            <p className="mx-auto mt-4 max-w-md text-lg text-ink-muted">
              Enter a PR URL to start an interactive review session. Ask questions,
              resolve findings, and get context-aware answers.
            </p>
          </div>

          {/* URL input */}
          <div className="mt-10">
            {error && (
              <div className="mb-4 rounded-lg border border-red-400/30 bg-red-400/5 px-4 py-3">
                <p className="font-mono text-xs text-red-400">{error}</p>
              </div>
            )}
            <div className="flex items-center gap-3">
              <input
                type="text"
                value={prUrl}
                onChange={(e) => setPrUrl(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="https://github.com/owner/repo/pull/123"
                className="flex-1 rounded-lg border border-yard-700 bg-yard-900 px-4 py-3 font-mono text-sm text-ink outline-none transition placeholder:text-ink-faint focus:border-haul"
              />
              <button
                onClick={startReview}
                disabled={!prUrl.trim()}
                className="rounded-lg bg-haul px-6 py-3 font-mono text-sm font-medium text-white shadow-glow-blue transition hover:bg-haul-dim disabled:opacity-50"
              >
                Start Review
              </button>
            </div>
          </div>

          {/* Example PRs */}
          <div className="mt-8 text-center">
            <p className="font-mono text-xs text-ink-faint">Try an example PR:</p>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {EXAMPLE_PRS.map((ex) => (
                <button
                  key={ex.url}
                  onClick={() => setPrUrl(ex.url)}
                  className="rounded-full border border-yard-700 bg-yard-800/50 px-3 py-1.5 font-mono text-xs text-ink-muted transition hover:border-haul hover:text-ink"
                >
                  {ex.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </main>

      <footer className="border-t border-yard-700/60 py-6 text-center">
        <p className="font-mono text-xs text-ink-faint">
          Powered by PR-Agent &middot; AI-generated code review
        </p>
      </footer>
    </div>
  );
}
