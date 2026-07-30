"""
pr_agent/sessions/session.py

Data model for the interactive Prompting Agent.

Defines the core dataclasses used to track a multi-turn code-review
discussion between a developer and the agent:

    - ChatMessage    : a single turn in the conversation
    - Finding        : an issue identified in the PR diff, and its
                        discussion/resolution state
    - ReviewSession   : the aggregate root tying a PR review to its
                        conversation history and findings

These are plain dataclasses (no ORM / persistence logic here) so they can be
kept in-memory by SessionManager for the MVP, and later swapped for a
Redis-backed or DB-backed store without changing calling code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #

class ChatRole(str, Enum):
    """Who authored a given ChatMessage."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class FindingSeverity(str, Enum):
    """Severity classification for a Finding."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingStatus(str, Enum):
    """Lifecycle state of a Finding as the discussion progresses."""
    OPEN = "open"
    DISCUSSED = "discussed"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


# --------------------------------------------------------------------------- #
# ChatMessage
# --------------------------------------------------------------------------- #

@dataclass
class ChatMessage:
    """A single turn in the conversation between developer and agent."""

    role: ChatRole
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Allow role to be passed as a plain string ("user", "assistant", ...)
        if not isinstance(self.role, ChatRole):
            self.role = ChatRole(self.role.lower())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        timestamp = data.get("timestamp")
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(timestamp) if timestamp else datetime.now(timezone.utc),
            metadata=data.get("metadata", {}) or {},
        )


# --------------------------------------------------------------------------- #
# Finding
# --------------------------------------------------------------------------- #

@dataclass
class Finding:
    """An issue identified by the agent in the PR diff."""

    file: str
    start_line: int
    severity: FindingSeverity
    category: str
    explanation: str
    id: str = field(default_factory=lambda: f"f_{uuid.uuid4().hex[:8]}")
    end_line: Optional[int] = None
    question_for_developer: Optional[str] = None
    confidence: float = 1.0
    status: FindingStatus = FindingStatus.OPEN
    resolution_note: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.severity, FindingSeverity):
            self.severity = FindingSeverity(self.severity.lower())
        if not isinstance(self.status, FindingStatus):
            self.status = FindingStatus(self.status.lower())
        if self.end_line is None:
            self.end_line = self.start_line

    def mark(self, status: FindingStatus, note: Optional[str] = None) -> None:
        """Transition this finding to a new status, optionally with a note."""
        if not isinstance(status, FindingStatus):
            status = FindingStatus(status)
        self.status = status
        if note is not None:
            self.resolution_note = note

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "severity": self.severity.value,
            "category": self.category,
            "explanation": self.explanation,
            "question_for_developer": self.question_for_developer,
            "confidence": self.confidence,
            "status": self.status.value,
            "resolution_note": self.resolution_note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Finding":
        return cls(
            id=data.get("id") or f"f_{uuid.uuid4().hex[:8]}",
            file=data["file"],
            start_line=data["start_line"],
            end_line=data.get("end_line"),
            severity=data["severity"],
            category=data["category"],
            explanation=data["explanation"],
            question_for_developer=data.get("question_for_developer"),
            confidence=data.get("confidence", 1.0),
            status=data.get("status", FindingStatus.OPEN),
            resolution_note=data.get("resolution_note"),
        )


# --------------------------------------------------------------------------- #
# ReviewSession
# --------------------------------------------------------------------------- #

@dataclass
class ReviewSession:
    """
    Aggregate root for a single interactive review conversation.

    One ReviewSession corresponds to one PR being discussed by one
    developer in the web UI. SessionManager owns the collection of these.
    """

    pr_url: str
    diff_content: str
    pr_metadata: Dict[str, Any] = field(default_factory=dict)

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    conversation_history: List[ChatMessage] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    current_finding_id: Optional[str] = None
    turn_count: int = 0

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_minutes: int = 60

    # ------------------------------------------------------------------ #
    # Conversation helpers
    # ------------------------------------------------------------------ #

    def add_message(
        self,
        role: ChatRole | str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ChatMessage:
        """Append a message to the conversation history and touch the session."""
        message = ChatMessage(role=role, content=content, metadata=metadata or {})
        self.conversation_history.append(message)
        if message.role in (ChatRole.USER, ChatRole.ASSISTANT):
            self.turn_count += 1
        self.touch()
        return message

    def get_recent_messages(self, max_turns: int = 20) -> List[ChatMessage]:
        """Return the last `max_turns` messages (most recent conversation window)."""
        if max_turns <= 0:
            return []
        return self.conversation_history[-max_turns:]

    # ------------------------------------------------------------------ #
    # Finding helpers
    # ------------------------------------------------------------------ #

    def add_finding(self, finding: Finding) -> Finding:
        self.findings.append(finding)
        return finding

    def get_finding(self, finding_id: str) -> Optional[Finding]:
        return next((f for f in self.findings if f.id == finding_id), None)

    def get_active_findings(self) -> List[Finding]:
        """Findings that are not yet resolved or dismissed."""
        return [
            f for f in self.findings
            if f.status in (FindingStatus.OPEN, FindingStatus.DISCUSSED)
        ]

    def update_finding(self, finding_id: str, **kwargs: Any) -> Optional[Finding]:
        """Update fields on a finding in place. Returns the finding, or None if not found."""
        finding = self.get_finding(finding_id)
        if finding is None:
            return None
        for key, value in kwargs.items():
            if key == "status" and not isinstance(value, FindingStatus):
                value = FindingStatus(value.lower())
            if key == "severity" and not isinstance(value, FindingSeverity):
                value = FindingSeverity(value.lower())
            setattr(finding, key, value)
        self.touch()
        return finding

    def next_open_finding(self) -> Optional[Finding]:
        """Advance current_finding_id to the next open finding, and return it."""
        open_findings = [f for f in self.findings if f.status == FindingStatus.OPEN]
        if not open_findings:
            self.current_finding_id = None
            return None
        self.current_finding_id = open_findings[0].id
        return open_findings[0]

    # ------------------------------------------------------------------ #
    # Lifecycle helpers
    # ------------------------------------------------------------------ #

    def touch(self) -> None:
        """Mark the session as recently active (resets TTL expiry clock)."""
        self.last_active = datetime.now(timezone.utc)

    def is_expired(self) -> bool:
        elapsed_minutes = (datetime.now(timezone.utc) - self.last_active).total_seconds() / 60
        return elapsed_minutes >= self.ttl_minutes

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "pr_url": self.pr_url,
            "pr_metadata": self.pr_metadata,
            "conversation_history": [m.to_dict() for m in self.conversation_history],
            "findings": [f.to_dict() for f in self.findings],
            "current_finding_id": self.current_finding_id,
            "turn_count": self.turn_count,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
            "ttl_minutes": self.ttl_minutes,
        }
