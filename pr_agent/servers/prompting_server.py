"""
pr_agent/servers/prompting_server.py

FastAPI + SSE web server for the interactive Prompting Agent (plan section
5.4). Follows the same pattern as pr_agent/servers/github_app.py: a FastAPI
app, background tasks, and settings pulled from the [prompting_agent.web]
config section.

Endpoints (all implemented below):
    GET    /                                    -> static web UI (pr_agent/web)
    POST   /api/v1/sessions                     -> create session, run initial analysis
    GET    /api/v1/sessions/{session_id}         -> session state
    POST   /api/v1/sessions/{session_id}/messages -> queue a developer message
    POST   /api/v1/sessions/{session_id}/cancel   -> cancel in-flight generation
    DELETE /api/v1/sessions/{session_id}          -> close session, return summary
    GET    /streams/{session_id}                 -> SSE stream of agent responses
    GET    /api/v1/sessions/{session_id}/diff     -> diff + finding annotations

Run locally:
    uvicorn pr_agent.servers.prompting_server:app --reload --port 8090
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pr_agent.sessions.session_manager import SessionManager, SessionNotFoundError
from pr_agent.sessions.mongo_store import build_mongo_store
from pr_agent.algo.ai_handlers.litellm_ai_handler import LiteLLMAIHandler
from pr_agent.git_providers import get_git_provider_with_context
from pr_agent.config_loader import get_settings
from pr_agent.tools.pr_prompting_agent import PRPromptingAgent

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

session_manager = SessionManager(default_ttl_minutes=60, cleanup_interval_seconds=300)

# session_id -> asyncio.Queue of SSE events, populated by an active handle_message() run
_stream_queues: Dict[str, "asyncio.Queue[Optional[dict]]"] = {}
# session_id -> asyncio.Task for the in-flight generation, so /cancel can cancel it
_active_tasks: Dict[str, asyncio.Task] = {}


class _GitProviderAdapter:
    """Wraps a real GitProvider to match the GitProviderProtocol used by PRPromptingAgent."""

    def __init__(self, pr_url: str):
        self._gp = get_git_provider_with_context(pr_url)
        self._pr_url = pr_url

    def get_pr_diff(self) -> str:
        """Build a simple unified diff string from the git provider's file patches."""
        parts = []
        for f in self._gp.get_diff_files():
            if f.patch:
                parts.append(f"## File: '{f.filename}'")
                parts.append(f.patch)
                parts.append("")
        return "\n".join(parts) if parts else self._gp.get_files()

    def get_pr_metadata(self) -> dict:
        return {
            "title": self._gp.pr.title,
            "branch": self._gp.get_pr_branch(),
            "description": self._gp.get_pr_description(),
            "language": self._gp.get_languages(),
        }


def build_agent(pr_url: str) -> PRPromptingAgent:
    git_provider = _GitProviderAdapter(pr_url)
    ai_handler = LiteLLMAIHandler()
    return PRPromptingAgent(
        pr_url=pr_url,
        git_provider=git_provider,
        ai_handler=ai_handler,
        session_manager=session_manager,
        model=get_settings().prompting_agent.model,
    )


# --------------------------------------------------------------------------- #
# App setup
# --------------------------------------------------------------------------- #

app = FastAPI(title="PR-Agent Prompting Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten via [prompting_agent.web].cors_origins in production
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    # Attach MongoDB persistence (no-op in-memory when no URI is configured).
    session_manager.store = build_mongo_store()
    if session_manager.store is not None and session_manager.store.available:
        session_manager.load_from_store()
    await session_manager.start_cleanup_task()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await session_manager.stop_cleanup_task()
    if session_manager.store is not None:
        session_manager.store.flush()


# --------------------------------------------------------------------------- #
# Request/response models
# --------------------------------------------------------------------------- #

class CreateSessionRequest(BaseModel):
    pr_url: str = Field(..., description="URL of the pull request to review")


class CreateSessionResponse(BaseModel):
    session_id: str
    status: str


class MessageRequest(BaseModel):
    content: str = Field(..., min_length=1)


class MessageResponse(BaseModel):
    stream_url: str


class CancelResponse(BaseModel):
    status: str


class CloseResponse(BaseModel):
    status: str
    summary: str


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@app.post("/api/v1/sessions", response_model=CreateSessionResponse)
async def create_session(body: CreateSessionRequest) -> CreateSessionResponse:
    agent = build_agent(body.pr_url)
    try:
        session_id = await agent.run()
    except Exception:
        logger.exception("Initial analysis failed for %s", body.pr_url)
        raise HTTPException(status_code=502, detail="Initial PR analysis failed")
    return CreateSessionResponse(session_id=session_id, status="ready")


@app.get("/api/v1/sessions/{session_id}")
async def get_session(session_id: str) -> Dict[str, Any]:
    try:
        session = session_manager.get_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return session.to_dict()


@app.get("/api/v1/sessions/{session_id}/diff")
async def get_diff(session_id: str) -> Dict[str, Any]:
    try:
        session = session_manager.get_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    annotations = [
        {
            "finding_id": f.id,
            "file": f.file,
            "start_line": f.start_line,
            "end_line": f.end_line,
            "severity": f.severity.value,
            "status": f.status.value,
        }
        for f in session.findings
    ]
    return {"diff": session.diff_content, "annotations": annotations}


@app.post("/api/v1/sessions/{session_id}/messages", response_model=MessageResponse)
async def post_message(session_id: str, body: MessageRequest) -> MessageResponse:
    try:
        session_manager.get_session(session_id)  # validates existence/expiry up front
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    agent = build_agent(session_manager.get_session(session_id).pr_url)
    queue: "asyncio.Queue[Optional[dict]]" = asyncio.Queue()
    _stream_queues[session_id] = queue

    async def produce() -> None:
        try:
            async for event in agent.handle_message(session_id, body.content):
                await queue.put(event)
        except asyncio.CancelledError:
            await queue.put({"event": "error", "data": {"message": "cancelled"}})
        finally:
            await queue.put(None)  # sentinel: stream complete

    task = asyncio.create_task(produce())
    _active_tasks[session_id] = task

    return MessageResponse(stream_url=f"/streams/{session_id}")


@app.post("/api/v1/sessions/{session_id}/cancel", response_model=CancelResponse)
async def cancel_generation(session_id: str) -> CancelResponse:
    task = _active_tasks.get(session_id)
    if task and not task.done():
        task.cancel()
        return CancelResponse(status="cancelled")
    return CancelResponse(status="no_active_generation")


@app.delete("/api/v1/sessions/{session_id}", response_model=CloseResponse)
async def close_session(session_id: str) -> CloseResponse:
    try:
        session_manager.get_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    agent = build_agent(session_manager.get_session(session_id).pr_url)
    try:
        summary = await agent.summarize(session_id)
    except Exception:
        logger.exception("Summary generation failed for session %s", session_id)
        summary = "Summary unavailable."

    session_manager.close_session(session_id)
    _stream_queues.pop(session_id, None)
    _active_tasks.pop(session_id, None)

    return CloseResponse(status="closed", summary=summary)


@app.get("/streams/{session_id}")
async def stream_session(session_id: str) -> StreamingResponse:
    queue = _stream_queues.get(session_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="No active stream for this session")

    async def event_source() -> AsyncGenerator[str, None]:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


# --------------------------------------------------------------------------- #
# Static web UI (must be mounted last so it doesn't shadow the API routes)
# --------------------------------------------------------------------------- #

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
