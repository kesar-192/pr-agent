"""
pr_agent/sessions/mongo_store.py

MongoDB persistence backend for the Prompting Agent.

Stores every ReviewSession as a single document (``_id = session_id``) in the
configured collection. SessionManager uses this as an optional write-through
store: the in-memory dict stays the fast read path, while every mutation is
mirrored to MongoDB. On app startup the server loads all documents back into
SessionManager, so history survives container restarts.

The MongoDB container (see docker/mongo/entrypoint.sh) additionally exports
this collection to a host-mounted JSON file every 60 seconds and restores it
on startup, giving a failure-proof backup outside the container.

If MongoDB is unreachable at construction time the store logs a warning and
becomes a no-op, so local development without Docker keeps working.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pr_agent.sessions.session import ReviewSession

logger = logging.getLogger(__name__)


class MongoSessionStore:
    """MongoDB-backed store of ReviewSession documents."""

    def __init__(
        self,
        mongodb_uri: str,
        database_name: str = "prompting_agent",
        collection_name: str = "sessions",
    ) -> None:
        self.mongodb_uri = mongodb_uri
        self.database_name = database_name
        self.collection_name = collection_name
        self._collection = None
        self._available = False
        self._connect()

    def _connect(self) -> None:
        try:
            from pymongo import ASCENDING, MongoClient

            self._client = MongoClient(
                self.mongodb_uri,
                serverSelectionTimeoutMS=2000,
                connectTimeoutMS=2000,
            )
            # Force a connection attempt so an unreachable MongoDB fails fast.
            self._client.admin.command("ping")
            collection = self._client[self.database_name][self.collection_name]
            # Unique index makes mongoimport --mode upsert idempotent by session_id.
            collection.create_index([("session_id", ASCENDING)], unique=True)
            self._collection = collection
            self._available = True
            logger.info(
                "MongoSessionStore connected to %s.%s",
                self.database_name,
                self.collection_name,
            )
        except Exception:
            self._client = None
            self._available = False
            logger.warning(
                "MongoDB unavailable at %r - sessions will only be stored in memory",
                self.mongodb_uri,
                exc_info=True,
            )

    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------ #
    # Write-through helpers
    # ------------------------------------------------------------------ #

    def _upsert(self, document: Dict[str, Any]) -> None:
        if not self._available:
            return
        try:
            self._collection.update_one(
                {"session_id": document["session_id"]},
                {"$set": document},
                upsert=True,
            )
        except Exception:
            logger.exception("Failed to persist session %s", document.get("session_id"))

    def save_session(self, session: ReviewSession) -> None:
        self._upsert(session.to_persistable_dict())

    def delete_session(self, session_id: str) -> None:
        if not self._available:
            return
        try:
            self._collection.delete_one({"session_id": session_id})
        except Exception:
            logger.exception("Failed to delete session %s from MongoDB", session_id)

    def load_all_sessions(self) -> List[ReviewSession]:
        """Load every stored session back into memory (called at startup)."""
        if not self._available:
            return []
        sessions: List[ReviewSession] = []
        try:
            for doc in self._collection.find():
                try:
                    sessions.append(ReviewSession.from_persistable_dict(doc))
                except Exception:
                    logger.exception("Skipping unreadable stored session %s", doc.get("session_id"))
        except Exception:
            logger.exception("Failed to load sessions from MongoDB")
        logger.info("Loaded %d session(s) from MongoDB", len(sessions))
        return sessions

    def flush(self) -> None:
        """Persist any final state and close the connection (called on shutdown)."""
        if self._available:
            try:
                self._client.close()
            except Exception:
                pass
        self._available = False
        self._collection = None

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"MongoSessionStore(uri={self.mongodb_uri!r}, "
            f"db={self.database_name!r}, collection={self.collection_name!r}, "
            f"available={self._available})"
        )


def build_mongo_store() -> Optional[MongoSessionStore]:
    """Build a store from settings (or the MONGO_URI env var), or None when unset."""
    import os

    from pr_agent.config_loader import get_settings

    try:
        storage = get_settings().prompting_agent.get("storage", {})
    except Exception:
        storage = {}
    # MONGO_URI env var (used by docker-compose) takes precedence.
    mongodb_uri = os.environ.get("MONGO_URI") or ""
    if not mongodb_uri:
        mongodb_uri = (storage.get("mongodb_uri") or "").strip()
    if not mongodb_uri:
        return None
    return MongoSessionStore(
        mongodb_uri=mongodb_uri,
        database_name=storage.get("database_name", "prompting_agent"),
        collection_name=storage.get("collection_name", "sessions"),
    )
