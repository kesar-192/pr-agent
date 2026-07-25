import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from pr_agent.config_loader import get_settings
from pr_agent.log import get_logger
from pr_agent.session.session import ChatMessage, Finding, FindingSeverity, FindingStatus, ReviewSession


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class SessionManager:
    """In-memory store for interactive review sessions.

    Sessions are keyed by a UUID string and held in a plain dict.
    Every access refreshes ``last_active`` so that actively-used sessions
    survive while idle ones expire via :meth:`cleanup_expired`.
    """

    def __init__(self):
        self._sessions: Dict[str, ReviewSession] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running: bool = False

    # -- public API ---------------------------------------------------------

    def create_session(self, pr_url: str, diff_content: str, pr_metadata: dict) -> str:
        """Allocate a new review session and return its ID."""
        session_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)

        ttl = 60
        try:
            ttl = get_settings().prompting_agent.session_ttl_minutes
        except Exception:
            pass

        session = ReviewSession(
            session_id=session_id,
            pr_url=pr_url,
            diff_content=diff_content,
            pr_metadata=pr_metadata,
            created_at=now,
            last_active=now,
            ttl_minutes=ttl,
        )

        self._sessions[session_id] = session
        get_logger().info(f"Created session {session_id} for {pr_url}")
        return session_id

    def get_session(self, session_id: str) -> ReviewSession:
        """Return a session by ID, refreshing its TTL timestamp.

        Raises ``ValueError`` when the ID is unknown.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        if self._is_expired(session):
            del self._sessions[session_id]
            get_logger().info(f"Session {session_id} expired, removed")
            raise ValueError(f"Session {session_id} has expired")

        session.last_active = datetime.now(timezone.utc)
        return session

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> ChatMessage:
        """Append a chat message to the session's conversation history."""
        session = self.get_session(session_id)

        message = ChatMessage(
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc),
            metadata=metadata or {},
        )
        session.conversation_history.append(message)
        session.turn_count += 1

        get_logger().debug(f"Session {session_id}: appended {role} message (turn {session.turn_count})")
        return message

    def get_conversation_context(self, session_id: str, max_turns: int = 20) -> str:
        """Build a formatted string of the last *max_turns* messages.

        The returned string is intended for injection into the discussion
        prompt template that is sent to the LLM.
        """
        session = self.get_session(session_id)

        try:
            max_turns = get_settings().prompting_agent.max_context_turns
        except Exception:
            pass

        if max_turns <= 0:
            max_turns = 20

        history = session.conversation_history[-max_turns:]
        lines: List[str] = []

        for idx, msg in enumerate(history, start=session.turn_count - len(history) + 1):
            lines.append(f"[Turn {idx} - {msg.role}]: {msg.content}")

        if session.current_finding_id:
            finding = self._find_by_id(session, session.current_finding_id)
            if finding:
                lines.append("")
                lines.append("[Current Finding]")
                lines.append(f"- File: {finding.file}, Lines: {finding.start_line}-{finding.end_line}")
                lines.append(f"- Severity: {finding.severity}")
                lines.append(f"- Category: {finding.category}")
                lines.append(f"- Explanation: {finding.explanation}")
                lines.append(f"- Status: {finding.status}")

        return "\n".join(lines)

    def update_finding(self, session_id: str, finding_id: str, **kwargs) -> Optional[Finding]:
        """Update one or more attributes on an existing finding."""
        session = self.get_session(session_id)
        finding = self._find_by_id(session, finding_id)

        if finding is None:
            raise ValueError(f"Finding {finding_id} not found in session {session_id}")

        for key, value in kwargs.items():
            if not hasattr(finding, key):
                get_logger().warning(f"Session {session_id}: finding {finding_id} has no attribute '{key}'")
                continue
            if key == "status" and not isinstance(value, FindingStatus):
                value = FindingStatus(value)
            if key == "severity" and not isinstance(value, FindingSeverity):
                value = FindingSeverity(value)
            setattr(finding, key, value)
            get_logger().debug(f"Session {session_id}: finding {finding_id} updated {key}={value}")
        session.touch()
        return finding

    def get_active_findings(self, session_id: str) -> List[Finding]:
        """Return findings whose status is *open* or *discussed*."""
        session = self.get_session(session_id)
        return [f for f in session.findings if f.status in (FindingStatus.OPEN, FindingStatus.DISCUSSED)]

    def close_session(self, session_id: str) -> None:
        """Remove a session from memory."""
        if session_id not in self._sessions:
            raise ValueError(f"Session {session_id} not found")

        del self._sessions[session_id]
        get_logger().info(f"Closed session {session_id}")

    # -- cleanup ------------------------------------------------------------

    def start_cleanup_task(self) -> None:
        """Launch the background cleanup loop (call once at server startup)."""
        if self._cleanup_task is not None:
            return
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        get_logger().info("Session cleanup task started")

    def stop_cleanup_task(self) -> None:
        """Signal the cleanup loop to stop (call on server shutdown)."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()

    def cleanup_expired(self) -> int:
        """Remove all sessions whose TTL has elapsed.  Returns count removed."""
        now = datetime.now(timezone.utc)
        expired_ids = [
            sid
            for sid, session in self._sessions.items()
            if now - session.last_active > timedelta(minutes=session.ttl_minutes)
        ]
        for sid in expired_ids:
            del self._sessions[sid]
            get_logger().info(f"Cleaned up expired session {sid}")

        return len(expired_ids)

    # -- private helpers ----------------------------------------------------

    async def _cleanup_loop(self) -> None:
        """Background loop that runs :meth:`cleanup_expired` every 5 minutes."""
        try:
            while self._running:
                await asyncio.sleep(300)
                count = self.cleanup_expired()
                if count:
                    get_logger().info(f"Cleanup: removed {count} expired session(s)")
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _is_expired(session: ReviewSession) -> bool:
        now = datetime.now(timezone.utc)
        return now - session.last_active > timedelta(minutes=session.ttl_minutes)

    @staticmethod
    def _find_by_id(session: ReviewSession, finding_id: str) -> Optional[Finding]:
        for f in session.findings:
            if f.id == finding_id:
                return f
        return None
