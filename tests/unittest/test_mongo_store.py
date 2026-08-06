import unittest.mock as mock

from pr_agent.sessions.mongo_store import MongoSessionStore
from pr_agent.sessions.session import ChatRole, Finding, ReviewSession
from pr_agent.sessions.session_manager import SessionManager


class _FakeStore:
    """In-memory stand-in for MongoSessionStore to test SessionManager write-through."""

    def __init__(self):
        self.saved = {}
        self.deleted = []

    def save_session(self, session):
        self.saved[session.session_id] = session.to_persistable_dict()

    def delete_session(self, session_id):
        self.saved.pop(session_id, None)
        self.deleted.append(session_id)

    def load_all_sessions(self):
        return [ReviewSession.from_persistable_dict(d) for d in self.saved.values()]


# ----------------------------------------------------------------------- #
# ReviewSession persistence serialization
# ----------------------------------------------------------------------- #


class TestReviewSessionPersistence:
    def _make_session(self):
        session = ReviewSession(
            pr_url="https://github.com/owner/repo/pull/1",
            diff_content="@@ -1 +1 @@\n-old\n+new\n",
            pr_metadata={"title": "Fix bug"},
        )
        session.add_message(ChatRole.USER, "why does this fail?")
        session.add_finding(
            Finding(file="app.py", start_line=1, severity="high", category="bug", explanation="nope")
        )
        return session

    def test_persistable_dict_includes_diff_content(self):
        session = self._make_session()
        data = session.to_persistable_dict()
        assert "diff_content" in data
        assert data["diff_content"] == session.diff_content

    def test_roundtrip_preserves_conversation(self):
        session = self._make_session()
        restored = ReviewSession.from_persistable_dict(session.to_persistable_dict())
        assert restored.session_id == session.session_id
        assert restored.pr_url == session.pr_url
        assert restored.diff_content == session.diff_content
        assert restored.pr_metadata == session.pr_metadata
        assert len(restored.conversation_history) == 1
        assert restored.conversation_history[0].role == ChatRole.USER
        assert restored.conversation_history[0].content == "why does this fail?"
        assert restored.turn_count == session.turn_count

    def test_roundtrip_preserves_findings(self):
        session = self._make_session()
        restored = ReviewSession.from_persistable_dict(session.to_persistable_dict())
        assert len(restored.findings) == 1
        assert restored.findings[0].file == "app.py"
        assert restored.findings[0].status.value == "open"

    def test_from_persistable_dict_handles_empty_lists(self):
        session = ReviewSession(pr_url="https://github.com/o/r/pull/2", diff_content="")
        restored = ReviewSession.from_persistable_dict(session.to_persistable_dict())
        assert restored.conversation_history == []
        assert restored.findings == []


# ----------------------------------------------------------------------- #
# SessionManager write-through to a durable store
# ----------------------------------------------------------------------- #


class TestSessionManagerWithStore:
    def test_create_session_is_persisted(self):
        store = _FakeStore()
        manager = SessionManager(store=store)
        sid = manager.create_session("https://github.com/owner/repo/pull/3", "diff")
        assert sid in store.saved

    def test_append_message_updates_stored_copy(self):
        store = _FakeStore()
        manager = SessionManager(store=store)
        sid = manager.create_session("https://github.com/owner/repo/pull/3", "diff")
        manager.append_message(sid, "user", "hello")
        assert store.saved[sid]["conversation_history"][0]["content"] == "hello"

    def test_close_session_deletes_from_store(self):
        store = _FakeStore()
        manager = SessionManager(store=store)
        sid = manager.create_session("https://github.com/owner/repo/pull/3", "diff")
        manager.close_session(sid)
        assert sid not in store.saved
        assert sid in store.deleted

    def test_load_from_store_restores_sessions(self):
        store = _FakeStore()
        manager = SessionManager(store=store)
        sid = manager.create_session("https://github.com/owner/repo/pull/4", "diff", pr_metadata={"title": "t"})
        manager.append_message(sid, "user", "persisted message")

        fresh_manager = SessionManager(store=store)
        count = fresh_manager.load_from_store()
        assert count == 1
        restored = fresh_manager.get_session(sid)
        assert restored.pr_url == "https://github.com/owner/repo/pull/4"
        assert restored.conversation_history[0].content == "persisted message"

    def test_no_store_stays_in_memory(self):
        manager = SessionManager()
        sid = manager.create_session("https://github.com/owner/repo/pull/5", "diff")
        assert manager.has_session(sid)
        assert manager.load_from_store() == 0


# ----------------------------------------------------------------------- #
# MongoSessionStore fallback when MongoDB is unreachable
# ----------------------------------------------------------------------- #


class TestMongoSessionStoreFallback:
    def test_unreachable_mongo_becomes_noop(self):
        with mock.patch("pymongo.MongoClient", side_effect=RuntimeError("no server")):
            store = MongoSessionStore("mongodb://127.0.0.1:1")
        assert store.available is False
        session = ReviewSession(pr_url="https://github.com/owner/repo/pull/6", diff_content="x")
        store.save_session(session)  # must not raise
        store.delete_session(session.session_id)  # must not raise
        assert store.load_all_sessions() == []

    def test_build_mongo_store_returns_none_without_uri(self, monkeypatch):
        monkeypatch.delenv("MONGO_URI", raising=False)
        from pr_agent.sessions.mongo_store import build_mongo_store
        assert build_mongo_store() is None
