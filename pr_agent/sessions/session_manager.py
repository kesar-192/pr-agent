"""
pr_agent/sessions/session_manager.py

In-memory session store for the Prompting Agent (MVP storage backend -
see the plan doc, section 8.1, for the future Redis-backed alternative).

SessionManager owns the lifecycle of every ReviewSession: creation,
lookup, message/finding mutation, and TTL-based expiry cleanup. It holds
no AI or git-provider logic itself - it is purely bookkeeping, which is
what lets pr_prompting_agent.py and prompting_server.py stay thin.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from pr_agent.sessions.session import ChatMessage, ChatRole, Finding, ReviewSession

logger = logging.getLogger(__name__)


class SessionNotFoundError(KeyError):
    """Raised when a session_id does not exist or has expired and been evicted."""


class SessionManager:
    """
    Thread-safe (asyncio-safe) in-memory store of ReviewSession objects.

    Usage:
        manager = SessionManager()
        session_id = manager.create_session(pr_url, diff_content, pr_metadata)
        manager.append_message(session_id, "user", "why is this a problem?")
        ...
        await manager.start_cleanup_task()   # run once at app startup
    """

    def __init__(
        self,
        default_ttl_minutes: int = 60,
        cleanup_interval_seconds: int = 300,
        store: Optional[Any] = None,
    ) -> None:
        self._sessions: Dict[str, ReviewSession] = {}
        self._lock = asyncio.Lock()
        self.default_ttl_minutes = default_ttl_minutes
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._cleanup_task: Optional[asyncio.Task] = None
        self.store = store

    def _persist(self, session: ReviewSession) -> None:
        """Write-through a session to the durable store (if one is configured)."""
        if self.store is not None:
            try:
                self.store.save_session(session)
            except Exception:
                logger.exception("Failed to persist session %s", session.session_id)

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #

    def create_session(
        self,
        pr_url: str,
        diff_content: str,
        pr_metadata: Optional[Dict[str, Any]] = None,
        ttl_minutes: Optional[int] = None,
    ) -> str:
        session = ReviewSession(
            pr_url=pr_url,
            diff_content=diff_content,
            pr_metadata=pr_metadata or {},
            ttl_minutes=ttl_minutes or self.default_ttl_minutes,
        )
        self._sessions[session.session_id] = session
        logger.info("Created session %s for %s", session.session_id, pr_url)
        self._persist(session)
        return session.session_id

    def get_session(self, session_id: str) -> ReviewSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if session.is_expired():
            self._sessions.pop(session_id, None)
            raise SessionNotFoundError(session_id)
        return session

    def has_session(self, session_id: str) -> bool:
        try:
            self.get_session(session_id)
            return True
        except SessionNotFoundError:
            return False

    def close_session(self, session_id: str) -> Optional[ReviewSession]:
        """Remove a session from the store and return it (e.g. for a final summary)."""
        session = self._sessions.pop(session_id, None)
        if session is not None and self.store is not None:
            try:
                self.store.delete_session(session_id)
            except Exception:
                logger.exception("Failed to delete session %s from store", session_id)
        return session

    # ------------------------------------------------------------------ #
    # Conversation
    # ------------------------------------------------------------------ #

    def append_message(
        self,
        session_id: str,
        role: ChatRole | str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ChatMessage:
        session = self.get_session(session_id)
        message = session.add_message(role, content, metadata)
        self._persist(session)
        return message

    def get_conversation_context(self, session_id: str, max_turns: int = 20) -> str:
        """Flatten recent conversation history into a single string for prompt rendering."""
        session = self.get_session(session_id)
        messages = session.get_recent_messages(max_turns)
        return "\n".join(f"{m.role.value}: {m.content}" for m in messages)

    # ------------------------------------------------------------------ #
    # Findings
    # ------------------------------------------------------------------ #

    def add_finding(self, session_id: str, finding: Finding) -> Finding:
        session = self.get_session(session_id)
        added = session.add_finding(finding)
        self._persist(session)
        return added

    def update_finding(self, session_id: str, finding_id: str, **kwargs: Any) -> Optional[Finding]:
        session = self.get_session(session_id)
        updated = session.update_finding(finding_id, **kwargs)
        if updated is not None:
            self._persist(session)
        return updated

    def get_active_findings(self, session_id: str) -> List[Finding]:
        session = self.get_session(session_id)
        return session.get_active_findings()

    # ------------------------------------------------------------------ #
    # Cleanup (TTL expiry)
    # ------------------------------------------------------------------ #

    def cleanup_expired(self) -> int:
        """Synchronously evict expired sessions. Returns the number removed."""
        expired_ids = [sid for sid, s in self._sessions.items() if s.is_expired()]
        for sid in expired_ids:
            session = self._sessions.pop(sid, None)
            if session is not None and self.store is not None:
                try:
                    self.store.delete_session(sid)
                except Exception:
                    logger.exception("Failed to delete session %s from store", sid)
        if expired_ids:
            logger.info("Evicted %d expired session(s)", len(expired_ids))
        return len(expired_ids)

    def load_from_store(self) -> int:
        """Reload previously persisted sessions (call once at app startup)."""
        if self.store is None:
            return 0
        loaded = 0
        for session in self.store.load_all_sessions():
            self._sessions[session.session_id] = session
            loaded += 1
        logger.info("Restored %d session(s) from durable store", loaded)
        return loaded

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cleanup_interval_seconds)
            try:
                self.cleanup_expired()
            except Exception:
                logger.exception("Session cleanup loop failed")

    async def start_cleanup_task(self) -> None:
        """Call once from a FastAPI startup event to begin periodic TTL eviction."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup_task(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None

    def __len__(self) -> int:
        return len(self._sessions)
