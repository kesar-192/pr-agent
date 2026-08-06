"""
tests/e2e_tests/test_mongo_store.py

Integration tests for MongoSessionStore against a REAL MongoDB instance.

These are skipped unless a MongoDB is reachable at MONGO_TEST_URI (defaults to
mongodb://localhost:27017), so the regular unit suite stays fast and hermetic.

Start a MongoDB the same way the prompting-agent stack does:

    docker run --rm -d -p 27017:27017 --name pr-agent-mongo-test mongo:7

or via the compose file:

    docker compose -f docker-compose.prompting-agent.yml up -d mongo-db

Each test uses its own randomly-named collection inside the
``prompting_agent_test`` database, which is dropped after the test so a local
run never pollutes the real ``prompting_agent`` database.
"""

import os
import uuid
from typing import Iterator

import pytest
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, InvalidOperation

from pr_agent.sessions.mongo_store import MongoSessionStore, build_mongo_store
from pr_agent.sessions.session import ChatRole, Finding, FindingSeverity, FindingStatus, ReviewSession

TEST_URI = os.environ.get("MONGO_TEST_URI", "mongodb://localhost:27017")
TEST_DB = "prompting_agent_test"


def _mongo_reachable(uri: str) -> bool:
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)
        client.admin.command("ping")
        client.close()
        return True
    except Exception:
        return False


requires_mongo = pytest.mark.skipif(
    not _mongo_reachable(TEST_URI),
    reason=f"No reachable MongoDB at {TEST_URI}; set MONGO_TEST_URI",
)


@pytest.fixture
def store() -> Iterator[MongoSessionStore]:
    """A store on an isolated, self-cleaning collection."""
    collection = f"test_{uuid.uuid4().hex[:12]}"
    mongo_store = MongoSessionStore(TEST_URI, database_name=TEST_DB, collection_name=collection)
    assert mongo_store.available, "MongoSessionStore should connect to a reachable MongoDB"
    try:
        yield mongo_store
    finally:
        # A fresh client for cleanup: some tests call store.flush() first,
        # which closes the store's own client.
        try:
            admin = MongoClient(TEST_URI, serverSelectionTimeoutMS=2000)
            admin[TEST_DB][collection].drop()
            admin.close()
        except Exception:
            pass
        try:
            mongo_store.flush()
        except Exception:
            pass


def _sample_session() -> ReviewSession:
    session = ReviewSession(
        pr_url="https://github.com/owner/repo/pull/1",
        diff_content="@@ -1 +1 @@\n-old\n+new\n",
        pr_metadata={"title": "Fix bug", "branch": "feature/x"},
    )
    session.add_message(ChatRole.USER, "why does this fail?")
    session.add_message(ChatRole.ASSISTANT, "root cause is an unbound query")
    session.add_finding(
        Finding(
            file="app.py",
            start_line=42,
            severity="high",
            category="security",
            explanation="SQL injection via string interpolation",
            status=FindingStatus.OPEN,
        )
    )
    return session


@requires_mongo
class TestMongoSessionStoreIntegration:
    def test_save_and_load_roundtrip(self, store):
        session = _sample_session()
        store.save_session(session)

        loaded = store.load_all_sessions()
        assert len(loaded) == 1
        restored = loaded[0]
        assert restored.session_id == session.session_id
        assert restored.pr_url == session.pr_url
        assert restored.diff_content == session.diff_content
        assert restored.pr_metadata == session.pr_metadata
        assert len(restored.conversation_history) == 2
        assert restored.conversation_history[0].role == ChatRole.USER
        assert restored.conversation_history[0].content == "why does this fail?"
        assert len(restored.findings) == 1
        assert restored.findings[0].file == "app.py"
        assert restored.findings[0].start_line == 42
        assert restored.findings[0].severity == FindingSeverity.HIGH
        assert restored.findings[0].status == FindingStatus.OPEN

    def test_upsert_by_session_id_is_idempotent(self, store):
        session = _sample_session()
        store.save_session(session)
        session.add_message(ChatRole.USER, "one more turn")
        store.save_session(session)

        raw = list(store._collection.find({"session_id": session.session_id}))
        assert len(raw) == 1
        assert raw[0]["turn_count"] == session.turn_count
        assert len(store.load_all_sessions()) == 1

    def test_update_overwrites_not_duplicates(self, store):
        session = _sample_session()
        store.save_session(session)
        session.update_finding(session.findings[0].id, status="resolved", resolution_note="fixed")
        store.save_session(session)

        raw = list(store._collection.find({"session_id": session.session_id}))
        assert len(raw) == 1
        assert raw[0]["findings"][0]["status"] == "resolved"
        assert raw[0]["findings"][0]["resolution_note"] == "fixed"

    def test_delete_session_removes_document(self, store):
        session = _sample_session()
        store.save_session(session)
        store.delete_session(session.session_id)
        assert store.load_all_sessions() == []
        assert store._collection.count_documents({}) == 0

    def test_unique_index_on_session_id(self, store):
        session = _sample_session()
        store.save_session(session)
        duplicate = session.to_persistable_dict()
        with pytest.raises(DuplicateKeyError):
            store._collection.insert_one(duplicate)

    def test_load_all_skips_corrupt_documents(self, store):
        store.save_session(_sample_session())
        store._collection.insert_one({"session_id": "orphan-doc", "nope": "unreadable"})
        loaded = store.load_all_sessions()
        assert len(loaded) == 1

    def test_flush_closes_connection(self, store):
        client = store._client
        store.flush()
        assert store.available is False
        with pytest.raises(InvalidOperation):
            client.admin.command("ping")


@requires_mongo
class TestBuildMongoStoreIntegration:
    def test_build_store_from_env_var(self, monkeypatch):
        monkeypatch.setenv("MONGO_URI", TEST_URI)
        store = build_mongo_store()
        assert store is not None
        assert store.available
        store.flush()

    def test_build_store_uses_database_and_collection_names(self, monkeypatch):
        monkeypatch.setenv("MONGO_URI", TEST_URI)
        store = build_mongo_store()
        assert store.database_name == "prompting_agent"
        assert store.collection_name == "sessions"
        store.flush()
