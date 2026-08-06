# PR-Agent — Prompting Agent

An interactive, multi-turn extension to [PR-Agent](https://github.com/the-pr-agent/pr-agent), the open-source AI code review tool.

Where PR-Agent's existing `/review` command is **one-shot** — the agent analyzes a PR once and posts a static comment — the Prompting Agent turns that into a **conversation**. It analyzes the diff, presents findings one at a time, explains its reasoning, asks clarifying questions about your intent, discusses trade-offs, and only writes a code fix after you've confirmed the approach. It ships with its own web UI: a diff viewer on one side, a chat sidebar on the other.

This is a new, additive module (`pr_agent/sessions/`, `pr_agent/tools/pr_prompting_agent.py`, `pr_agent/servers/prompting_server.py`, `pr_agent/web/`) — it does not modify any existing PR-Agent command, provider, or server.

---

## Table of Contents

- [Why this exists](#why-this-exists)
- [What's in this repo](#whats-in-this-repo)
- [Architecture](#architecture)
- [Quickstart](#quickstart)
  - [Run with Docker Compose](#run-with-docker-compose)
  - [Run as a standalone container](#run-as-a-standalone-container)
  - [Run locally without Docker](#run-locally-without-docker)
- [Using the web UI](#using-the-web-ui)
- [API reference](#api-reference)
- [Data model](#data-model)
- [Prompt templates](#prompt-templates)
- [Current limitation: mock providers](#current-limitation-mock-providers)
- [Environment variables](#environment-variables)
- [Roadmap](#roadmap)

---

## Why this exists

PR-Agent's core tools (`/review`, `/improve`, `/describe`, `/ask`) are fast and cheap because each one is a single LLM call. That's great for a quick pass, but it means:

- There's no discussion — the agent states findings, you read them, done.
- `/ask` has no memory between questions.
- The agent never explains *why* something is a problem in any depth, and never asks you anything before proposing a fix.

The Prompting Agent is deliberately the opposite: slower, conversational, and closer to how a senior engineer actually reviews a colleague's PR — read the diff, flag concerns, ask what you were going for, discuss alternatives, then write the fix once you both agree on it.

## What's in this repo

```
pr_agent/
├── sessions/
│   ├── session.py            # ChatMessage, Finding, ReviewSession dataclasses
│   └── session_manager.py    # In-memory session store, TTL cleanup
├── tools/
│   └── pr_prompting_agent.py # PRPromptingAgent — analysis / discussion / summary turns
├── servers/
│   └── prompting_server.py   # FastAPI + SSE server, all /api/v1/sessions endpoints
├── settings/
│   └── prompting_agent_prompts.toml  # System/user prompt templates (Jinja2)
├── web/
│   ├── index.html            # Dashboard shell: top navbar + 3-panel workspace
│   ├── style.css             # "yard-night" dark/light theme, glassmorphism, glow states
│   └── app.js                # Session management, diff rendering, SSE chat streaming
├── Dockerfile.prompting-agent
└── requirements-prompting-agent.txt

docker-compose.prompting-agent.yml
```

## Architecture

```
Browser (pr_agent/web/)
   │  POST /api/v1/sessions, /messages, /cancel  (REST)
   │  GET  /streams/{session_id}                 (SSE — EventSource)
   ▼
FastAPI server (prompting_server.py)
   │
   ├─ SessionManager (sessions/session_manager.py)
   │     in-memory ReviewSession store, TTL-based cleanup
   │
   └─ PRPromptingAgent (tools/pr_prompting_agent.py)
         ├─ renders prompts from prompting_agent_prompts.toml (Jinja2)
         ├─ calls an AI handler (chat_completion / chat_completion_stream)
         └─ calls a git provider (get_pr_diff / get_pr_metadata)
```

REST is used for anything the developer initiates (create session, send a message, cancel, close); SSE is used for the one thing that streams — the agent's reply — because it needs zero extra infrastructure (no WebSocket upgrade, works behind any proxy, auto-reconnects via the browser's native `EventSource`).

A session moves through three phases:

1. **Analysis** (`PRPromptingAgent.run`) — fetch the diff once, ask the model to identify issues, and store them as `Finding` objects.
2. **Discussion** (`PRPromptingAgent.handle_message`) — every message the developer sends is answered by streaming tokens back over SSE; the agent tracks which finding is "current" and updates its status as the conversation progresses.
3. **Summary** (`PRPromptingAgent.summarize`) — on session close, the agent produces a markdown wrap-up: what was resolved, what was dismissed, what's still pending.

## Quickstart

### Run with Docker Compose

From the repository root:

```bash
docker compose -f docker-compose.prompting-agent.yml up --build
```

Then open:

```
http://localhost:8090/
```

To stop it:

```bash
docker compose -f docker-compose.prompting-agent.yml down
```

### Run as a standalone container

```bash
docker build -f pr_agent/Dockerfile.prompting-agent -t pr-agent/prompting-agent:latest .
docker run --rm -p 8090:8090 pr-agent/prompting-agent:latest
```

### Run locally without Docker

```bash
pip install -r pr_agent/requirements-prompting-agent.txt
uvicorn pr_agent.servers.prompting_server:app --reload --port 8090
```

Either way, the server serves both the API and the static web UI from the same process — there's nothing else to start.

## Using the web UI

1. Paste a PR URL into the top navbar and click **Analyze**. The agent fetches the diff and runs its first-pass analysis.
2. The diff appears in the center panel with a collapsible file tree on the left; lines with findings are highlighted by severity and clickable.
3. The right-hand panel lists the findings ("manifest") and is where you talk to the agent — click a finding, or a highlighted diff line, to draft a question about it.
4. Send a message; the reply streams in token-by-token over SSE.
5. Click **End & summarize** when you're done — this calls `DELETE /api/v1/sessions/{id}`, which returns a markdown summary shown in a modal.
6. The **Sessions** dropdown in the navbar lists every session still open on the server (`GET /api/v1/sessions`), so you can resume or close any of them without losing your place.

## API reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/sessions` | List active sessions (summary: id, PR URL, turn count, open findings). |
| `POST` | `/api/v1/sessions` | Create a session. Body: `{"pr_url": "..."}`. Runs the initial analysis and returns `{"session_id", "status"}`. |
| `GET` | `/api/v1/sessions/{id}` | Full session state — metadata, findings, conversation history. |
| `GET` | `/api/v1/sessions/{id}/diff` | The diff text plus finding annotations (file/line/severity) for rendering. |
| `POST` | `/api/v1/sessions/{id}/messages` | Send a developer message. Body: `{"content": "..."}`. Returns `{"stream_url"}` to open for the reply. |
| `GET` | `/streams/{id}` | SSE stream for the in-flight reply. Events: `token`, `finding_update`, `error`, `done`. |
| `POST` | `/api/v1/sessions/{id}/cancel` | Cancel an in-flight generation. |
| `DELETE` | `/api/v1/sessions/{id}` | Close the session and return `{"status", "summary"}`. |

Example flow:

```bash
# 1. create a session
curl -X POST http://localhost:8090/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"pr_url":"https://github.com/owner/repo/pull/123"}'
# -> {"session_id": "...", "status": "ready"}

# 2. ask a question
curl -X POST http://localhost:8090/api/v1/sessions/<id>/messages \
  -H "Content-Type: application/json" \
  -d '{"content":"Why is this a problem?"}'
# -> {"stream_url": "/streams/<id>"}

# 3. read the reply
curl -N http://localhost:8090/streams/<id>

# 4. close it out
curl -X DELETE http://localhost:8090/api/v1/sessions/<id>
```

## Data model

Defined in `pr_agent/sessions/session.py`:

- **`ChatMessage`** — `role` (`user` / `assistant` / `system`), `content`, `timestamp`, `metadata`.
- **`Finding`** — `id`, `file`, `start_line`, `end_line`, `severity` (`critical`/`high`/`medium`/`low`), `category`, `explanation`, `question_for_developer`, `confidence`, `status` (`open`/`discussed`/`resolved`/`dismissed`).
- **`ReviewSession`** — `session_id`, `pr_url`, `diff_content`, `pr_metadata`, `conversation_history: list[ChatMessage]`, `findings: list[Finding]`, `current_finding_id`, `turn_count`, `created_at`, `last_active`, `ttl_minutes`.

`SessionManager` (`session_manager.py`) is an in-memory store keyed by `session_id`, with a background task that evicts sessions past their TTL (default 60 minutes).

## Prompt templates

`pr_agent/settings/prompting_agent_prompts.toml` holds three Jinja2-rendered prompt pairs:

- **`prompting_agent_analysis_prompt`** — first turn only. Identifies issues, explains root cause, classifies severity, and asks a clarifying question per finding — explicitly forbidden from proposing a fix at this stage. Includes anti-hallucination rules: only reference what's literally in the diff, flag uncertainty instead of stating it as fact.
- **`prompting_agent_discussion_prompt`** — every later turn. Branches behavior by developer intent ("why?", disagreement, "alternatives?", "fix it", "next finding").
- **`prompting_agent_summary_prompt`** — session close. Groups findings into Resolved / Dismissed / Pending and lists any decisions made along the way.

## Current limitation: mock providers

`build_agent()` in `prompting_server.py` currently wires up `MockGitProvider` and `MockAIHandler` (in `pr_prompting_agent.py`) instead of a real git provider and a real LLM. This is intentional for now — it means the whole session lifecycle (analysis → discussion → SSE streaming → summary) runs and is testable with zero external credentials.

To go live, swap the two lines inside `build_agent()`:

```python
# from:
git_provider = MockGitProvider(pr_url)
ai_handler = MockAIHandler()

# to, e.g.:
from pr_agent.git_providers.utils import get_git_provider_with_context
from pr_agent.algo.ai_handlers.litellm_ai_handler import LiteLLMAIHandler
git_provider = get_git_provider_with_context(pr_url)
ai_handler = LiteLLMAIHandler()
```

Both `PRPromptingAgent` and `PromptRenderer` were written against the `GitProviderProtocol` / `AIHandlerProtocol` interfaces already used elsewhere in PR-Agent, so no other code needs to change.

## Environment variables

The compose file wires these in as optional — they aren't read by anything yet (see above), but exist so the switch to real providers doesn't require touching the Docker setup:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude models via `LiteLLMAIHandler`. |
| `OPENAI_API_KEY` | OpenAI models via `LiteLLMAIHandler`. |
| `GITHUB_TOKEN` | Auth for `GitProvider` implementations reading/posting to GitHub PRs. |

## Roadmap

- Swap `Mock*` classes for the real `LiteLLMAIHandler` / `GitProvider` stack (see above).
- Redis-backed `SessionManager` for persistence across restarts and multi-instance deployment.
- Register `prompting_agent` / `discuss` as CLI commands in `pr_agent/agent/pr_agent.py`, alongside `/review`, `/improve`, etc.
- Apply confirmed fixes directly via the git provider (PR suggestion commits) instead of only showing the diff in chat.
- Session replay / export as a markdown report for sharing with a team.

---

This module builds on [PR-Agent](https://github.com/the-pr-agent/pr-agent), which is community-maintained after being donated by Qodo to the open-source community. See the [upstream README](https://github.com/the-pr-agent/pr-agent) for the base project — supported git providers, deployment modes, and the existing one-shot tools (`/review`, `/improve`, `/describe`, `/ask`).
