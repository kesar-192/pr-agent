from datetime import datetime, timedelta, timezone

from pr_agent.sessions.session import (
    ChatMessage,
    ChatRole,
    Finding,
    FindingSeverity,
    FindingStatus,
    ReviewSession,
)
from pr_agent.sessions.session_manager import SessionManager


# ----------------------------------------------------------------------- #
# ChatMessage tests
# ----------------------------------------------------------------------- #


class TestChatMessage:
    def test_role_coercion_from_string(self):
        msg = ChatMessage(role="user", content="hello")
        assert msg.role == ChatRole.USER

    def test_default_metadata_is_empty_dict(self):
        msg = ChatMessage(role="assistant", content="hi")
        assert msg.metadata == {}

    def test_to_dict(self):
        msg = ChatMessage(role="user", content="check this")
        d = msg.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "check this"
        assert "timestamp" in d
        assert d["metadata"] == {}

    def test_from_dict_roundtrip(self):
        msg = ChatMessage(role="system", content="init", metadata={"k": "v"})
        d = msg.to_dict()
        msg2 = ChatMessage.from_dict(d)
        assert msg2.role == ChatRole.SYSTEM
        assert msg2.content == "init"
        assert msg2.metadata == {"k": "v"}

    def test_from_dict_missing_timestamp(self):
        msg = ChatMessage.from_dict({"role": "user", "content": "x"})
        assert isinstance(msg.timestamp, datetime)

    def test_from_dict_metadata_none(self):
        msg = ChatMessage.from_dict({"role": "user", "content": "x", "metadata": None})
        assert msg.metadata == {}


# ----------------------------------------------------------------------- #
# Finding tests
# ----------------------------------------------------------------------- #


class TestFinding:
    def _make_finding(self, **overrides):
        defaults = dict(file="app.py", start_line=10, severity="high", category="bug", explanation="off-by-one")
        defaults.update(overrides)
        return Finding(**defaults)

    def test_status_defaults_to_open(self):
        f = self._make_finding()
        assert f.status == FindingStatus.OPEN

    def test_end_line_defaults_to_start_line(self):
        f = self._make_finding()
        assert f.end_line == 10

    def test_end_line_explicit(self):
        f = self._make_finding(end_line=20)
        assert f.end_line == 20

    def test_mark_transition(self):
        f = self._make_finding()
        f.mark("resolved", note="Fixed")
        assert f.status == FindingStatus.RESOLVED
        assert f.resolution_note == "Fixed"

    def test_mark_without_note(self):
        f = self._make_finding()
        f.mark("dismissed")
        assert f.status == FindingStatus.DISMISSED
        assert f.resolution_note is None

    def test_to_dict(self):
        f = self._make_finding()
        d = f.to_dict()
        assert d["file"] == "app.py"
        assert d["severity"] == "high"
        assert d["status"] == "open"
        assert "id" in d

    def test_from_dict_roundtrip(self):
        f = self._make_finding(id="f_test1234", end_line=15, confidence=0.8)
        f.status = FindingStatus.DISCUSSED
        d = f.to_dict()
        f2 = Finding.from_dict(d)
        assert f2.id == "f_test1234"
        assert f2.end_line == 15
        assert f2.confidence == 0.8
        assert f2.status == FindingStatus.DISCUSSED

    def test_id_auto_generated(self):
        f1 = self._make_finding()
        f2 = self._make_finding()
        assert f1.id != f2.id
        assert f1.id.startswith("f_")


# ----------------------------------------------------------------------- #
# ReviewSession tests
# ----------------------------------------------------------------------- #


class TestReviewSession:
    def _make_session(self):
        return ReviewSession(pr_url="https://github.com/org/repo/pull/1", diff_content="--- a\n+++ b")

    def test_add_message(self):
        s = self._make_session()
        msg = s.add_message("user", "check this")
        assert msg.content == "check this"
        assert s.turn_count == 1

    def test_add_message_metadata_none(self):
        s = self._make_session()
        msg = s.add_message("assistant", "ok", metadata=None)
        assert msg.metadata == {}

    def test_add_message_system_does_not_increment_turn(self):
        s = self._make_session()
        s.add_message("system", "init")
        assert s.turn_count == 0

    def test_get_recent_messages(self):
        s = self._make_session()
        s.add_message("user", "a")
        s.add_message("assistant", "b")
        s.add_message("user", "c")
        recent = s.get_recent_messages(2)
        assert len(recent) == 2
        assert recent[0].content == "b"
        assert recent[1].content == "c"

    def test_get_recent_messages_zero_returns_empty(self):
        s = self._make_session()
        s.add_message("user", "a")
        assert s.get_recent_messages(0) == []

    def test_add_and_get_finding(self):
        s = self._make_session()
        f = Finding(file="x.py", start_line=5, severity="low", category="style", explanation="fmt")
        s.add_finding(f)
        assert s.get_finding(f.id) is f
        assert s.get_finding("nonexistent") is None

    def test_get_active_findings(self):
        s = self._make_session()
        f1 = Finding(file="a.py", start_line=1, severity="high", category="bug", explanation="x")
        f2 = Finding(file="b.py", start_line=2, severity="low", category="style", explanation="y")
        s.add_finding(f1)
        s.add_finding(f2)
        f1.mark("resolved")
        active = s.get_active_findings()
        assert len(active) == 1
        assert active[0] is f2

    def test_update_finding(self):
        s = self._make_session()
        f = Finding(file="a.py", start_line=1, severity="low", category="bug", explanation="x")
        s.add_finding(f)
        updated = s.update_finding(f.id, status="discussed", severity="high")
        assert updated.status == FindingStatus.DISCUSSED
        assert updated.severity == FindingSeverity.HIGH

    def test_update_finding_nonexistent_returns_none(self):
        s = self._make_session()
        assert s.update_finding("nope", status="open") is None

    def test_next_open_finding(self):
        s = self._make_session()
        f1 = Finding(file="a.py", start_line=1, severity="high", category="bug", explanation="x")
        f2 = Finding(file="b.py", start_line=2, severity="low", category="style", explanation="y")
        s.add_finding(f1)
        s.add_finding(f2)
        first = s.next_open_finding()
        assert first is f1
        f1.mark("resolved")
        second = s.next_open_finding()
        assert second is f2
        f2.mark("resolved")
        none = s.next_open_finding()
        assert none is None

    def test_touch_updates_last_active(self):
        s = self._make_session()
        old = s.last_active
        s.touch()
        assert s.last_active >= old

    def test_is_expired_false_initially(self):
        s = self._make_session()
        assert not s.is_expired()

    def test_is_expired_true(self):
        s = self._make_session()
        s.last_active = datetime.now(timezone.utc) - timedelta(minutes=120)
        assert s.is_expired()

    def test_to_dict_keys(self):
        s = self._make_session()
        sd = s.to_dict()
        expected = {"session_id", "pr_url", "diff_content", "pr_metadata",
                     "conversation_history", "findings", "current_finding_id",
                     "turn_count", "created_at", "last_active", "ttl_minutes"}
        assert set(sd.keys()) == expected
        assert sd["pr_url"] == "https://github.com/org/repo/pull/1"
        assert sd["diff_content"] == "--- a\n+++ b"


# ----------------------------------------------------------------------- #
# SessionManager tests
# ----------------------------------------------------------------------- #


class TestSessionManager:
    def test_create_and_get_session(self):
        sm = SessionManager()
        sid = sm.create_session("https://github.com/org/repo/pull/1", "diff", {"title": "Fix"})
        s = sm.get_session(sid)
        assert s.pr_url == "https://github.com/org/repo/pull/1"
        assert s.diff_content == "diff"

    def test_get_nonexistent_raises(self):
        sm = SessionManager()
        try:
            sm.get_session("nope")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_get_expired_session_removes_and_raises(self):
        sm = SessionManager()
        sid = sm.create_session("https://x", "d", {})
        s = sm.get_session(sid)
        s.last_active = datetime.now(timezone.utc) - timedelta(minutes=120)
        try:
            sm.get_session(sid)
            assert False
        except ValueError:
            pass

    def test_append_message(self):
        sm = SessionManager()
        sid = sm.create_session("https://x", "d", {})
        msg = sm.append_message(sid, "user", "hello")
        assert msg.content == "hello"
        assert msg.metadata == {}
        s = sm.get_session(sid)
        assert s.turn_count == 1

    def test_append_message_with_metadata(self):
        sm = SessionManager()
        sid = sm.create_session("https://x", "d", {})
        msg = sm.append_message(sid, "assistant", "ok", metadata={"tool": "review"})
        assert msg.metadata == {"tool": "review"}

    def test_get_conversation_context(self):
        sm = SessionManager()
        sid = sm.create_session("https://x", "d", {})
        sm.append_message(sid, "user", "Check this")
        sm.append_message(sid, "assistant", "Found issue")
        ctx = sm.get_conversation_context(sid)
        assert "[Turn" in ctx
        assert "Check this" in ctx
        assert "Found issue" in ctx

    def test_update_finding_type_coercion(self):
        sm = SessionManager()
        sid = sm.create_session("https://x", "d", {})
        s = sm.get_session(sid)
        f = Finding(file="a.py", start_line=1, severity="low", category="bug", explanation="x")
        s.add_finding(f)
        updated = sm.update_finding(sid, f.id, status="discussed", severity="high")
        assert updated.status == FindingStatus.DISCUSSED
        assert updated.severity == FindingSeverity.HIGH

    def test_update_finding_nonexistent_raises(self):
        sm = SessionManager()
        sid = sm.create_session("https://x", "d", {})
        try:
            sm.update_finding(sid, "nope", status="open")
            assert False
        except ValueError:
            pass

    def test_get_active_findings(self):
        sm = SessionManager()
        sid = sm.create_session("https://x", "d", {})
        s = sm.get_session(sid)
        f1 = Finding(file="a.py", start_line=1, severity="high", category="bug", explanation="x")
        f2 = Finding(file="b.py", start_line=2, severity="low", category="style", explanation="y")
        s.add_finding(f1)
        s.add_finding(f2)
        f1.mark("resolved")
        active = sm.get_active_findings(sid)
        assert len(active) == 1
        assert active[0] is f2

    def test_close_session(self):
        sm = SessionManager()
        sid = sm.create_session("https://x", "d", {})
        sm.close_session(sid)
        try:
            sm.get_session(sid)
            assert False
        except ValueError:
            pass

    def test_close_nonexistent_raises(self):
        sm = SessionManager()
        try:
            sm.close_session("nope")
            assert False
        except ValueError:
            pass

    def test_cleanup_expired_returns_zero_when_none_expired(self):
        sm = SessionManager()
        sm.create_session("https://x", "d", {})
        assert sm.cleanup_expired() == 0

    def test_cleanup_expired_removes_old_sessions(self):
        sm = SessionManager()
        sid = sm.create_session("https://x", "d", {})
        s = sm.get_session(sid)
        s.last_active = datetime.now(timezone.utc) - timedelta(minutes=120)
        assert sm.cleanup_expired() == 1
        try:
            sm.get_session(sid)
            assert False
        except ValueError:
            pass
