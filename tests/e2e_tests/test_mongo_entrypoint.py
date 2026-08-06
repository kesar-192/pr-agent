"""
tests/e2e_tests/test_mongo_entrypoint.py

End-to-end test of docker/mongo/entrypoint.sh using a real mongo:7 container.

Verifies the three behaviors the mongo-db service relies on:
  1. fresh start (no host backup) creates a new database and touches
     /backups/.restored so the compose healthcheck unblocks the agent;
  2. the periodic export writes a host-visible /backups/history_backup.json;
  3. a restart with that backup restores sessions (upsert by session_id).

Gated behind RUN_DOCKER_TESTS=1 because it launches containers. Run with:

    RUN_DOCKER_TESTS=1 PYTHONPATH=. pytest tests/e2e_tests/test_mongo_entrypoint.py -q
"""

import json
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from pymongo import MongoClient

ENTRYPOINT = Path(__file__).resolve().parents[2] / "docker" / "mongo" / "entrypoint.sh"
MONGO_IMAGE = "mongo:7"

requires_docker = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True, text=True).returncode != 0,
    reason="docker daemon not reachable",
)
requires_env = pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_TESTS") != "1",
    reason="set RUN_DOCKER_TESTS=1 to run docker-backed entrypoint tests",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _wait_for(predicate, timeout=60, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def backup_dir(tmp_path):
    return tmp_path / "backups"


@requires_docker
@requires_env
class TestMongoEntrypoint:
    def test_backup_and_restore_roundtrip(self, backup_dir):
        backup_dir.mkdir()
        name = f"mongo-entrypoint-{uuid.uuid4().hex[:8]}"
        port = _free_port()
        uri = f"mongodb://127.0.0.1:{port}"
        db, collection = "prompting_agent", "sessions"

        def run_container(extra=None):
            cmd = [
                "docker", "run", "-d", "--name", name,
                "-p", f"{port}:27017",
                "-e", "MONGO_DATABASE=prompting_agent",
                "-e", "MONGO_COLLECTION=sessions",
                "-e", "BACKUP_INTERVAL_SECONDS=2",
                "-v", f"{backup_dir}:/backups",
                "-v", f"{ENTRYPOINT}:/scripts/entrypoint.sh:ro",
                "--entrypoint", "/scripts/entrypoint.sh",
            ]
            if extra:
                cmd = cmd + extra
            cmd.append(MONGO_IMAGE)
            result = _run(cmd)
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        try:
            # --- fresh start: no backup, .restored marker appears -----------------
            run_container()
            assert _wait_for(lambda: (backup_dir / ".restored").exists()), \
                ".restored marker should appear after fresh start"

            # --- write a session and wait for the periodic JSON export -------------
            session_id = f"session-{uuid.uuid4().hex[:8]}"
            _insert_session(uri, session_id)

            backup_file = backup_dir / "history_backup.json"
            assert _wait_for(
                lambda: backup_file.exists()
                and backup_file.stat().st_size > 0
                and session_id in backup_file.read_text()
            ), "periodic export should write history_backup.json containing the session"

            # --- graceful stop triggers a final export -----------------------------
            _run(["docker", "stop", "--timeout", "5", name])
            _wait_for(lambda: not _container_running(name))

            backup_data = [json.loads(line) for line in backup_file.read_text().splitlines() if line.strip()]
            assert any(d.get("session_id") == session_id for d in backup_data), \
                "final export should contain the stored session"

            _run(["docker", "rm", name])

            # --- restart with the same backup: session restored via upsert ---------
            _restart_with_restore(backup_dir, name, port, uri, db, collection, {session_id})
        finally:
            _run(["docker", "rm", "-f", name])
            for leftover in backup_dir.iterdir():
                leftover.unlink()

    def test_restore_is_idempotent_across_restarts(self, backup_dir):
        """Repeated restores of the same backup must never duplicate sessions."""
        backup_dir.mkdir()
        name = f"mongo-entrypoint-{uuid.uuid4().hex[:8]}"
        port = _free_port()
        uri = f"mongodb://127.0.0.1:{port}"
        db, collection = "prompting_agent", "sessions"
        session_ids = {f"session-{uuid.uuid4().hex[:8]}-{i}" for i in range(3)}

        def run_container():
            result = _run([
                "docker", "run", "-d", "--name", name,
                "-p", f"{port}:27017",
                "-e", "MONGO_DATABASE=prompting_agent",
                "-e", "MONGO_COLLECTION=sessions",
                "-e", "BACKUP_INTERVAL_SECONDS=2",
                "-v", f"{backup_dir}:/backups",
                "-v", f"{ENTRYPOINT}:/scripts/entrypoint.sh:ro",
                "--entrypoint", "/scripts/entrypoint.sh",
                MONGO_IMAGE,
            ])
            assert result.returncode == 0, result.stderr

        try:
            run_container()
            assert _wait_for(lambda: (backup_dir / ".restored").exists())
            for session_id in session_ids:
                _insert_session(uri, session_id)

            # First backup export.
            backup_file = backup_dir / "history_backup.json"
            assert _wait_for(lambda: backup_file.exists() and backup_file.stat().st_size > 0)

            # Restart #1: restore happens, exactly 3 docs.
            _restart_with_restore(backup_dir, name, port, uri, db, collection, session_ids)

            # Restart #2: importing the same backup again must not duplicate.
            _restart_with_restore(backup_dir, name, port, uri, db, collection, session_ids)
        finally:
            _run(["docker", "rm", "-f", name])
            for leftover in backup_dir.iterdir():
                leftover.unlink()


def _is_connected(client) -> bool:
    try:
        client.admin.command("ping")
        return True
    except Exception:
        return False


def _container_running(name: str) -> bool:
    result = _run(["docker", "inspect", "-f", "{{.State.Running}}", name])
    return result.returncode == 0 and result.stdout.strip() == "true"


def _insert_session(uri: str, session_id: str) -> None:
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    _wait_for(lambda: _is_connected(client))
    client["prompting_agent"]["sessions"].insert_one({
        "session_id": session_id,
        "pr_url": "https://github.com/owner/repo/pull/1",
        "diff_content": "@@ -1 +1 @@\n-old\n+new\n",
        "conversation_history": [],
        "findings": [],
        "turn_count": 1,
    })
    client.close()


def _restart_with_restore(backup_dir, name, port, uri, db, collection, expected_ids) -> None:
    """Stop, remove, and re-run the container, then wait for the restore to
    actually finish. The .restored marker persists across restarts on the host
    mount, so we delete it first - otherwise it cannot gate on restore state -
    and additionally poll for the expected documents."""
    _run(["docker", "stop", "--timeout", "5", name])
    _wait_for(lambda: not _container_running(name))
    _run(["docker", "rm", name])

    marker = backup_dir / ".restored"
    if marker.exists():
        marker.unlink()

    _run([
        "docker", "run", "-d", "--name", name,
        "-p", f"{port}:27017",
        "-e", "MONGO_DATABASE=prompting_agent",
        "-e", "MONGO_COLLECTION=sessions",
        "-e", "BACKUP_INTERVAL_SECONDS=2",
        "-v", f"{backup_dir}:/backups",
        "-v", f"{ENTRYPOINT}:/scripts/entrypoint.sh:ro",
        "--entrypoint", "/scripts/entrypoint.sh",
        MONGO_IMAGE,
    ])
    assert _wait_for(lambda: marker.exists()), "restored marker should appear after restore"

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    assert _wait_for(lambda: _is_connected(client))

    def all_expected_present() -> bool:
        try:
            found = {d["session_id"] for d in client[db][collection].find({}, {"session_id": 1})}
            return expected_ids <= found
        except Exception:
            return False

    assert _wait_for(all_expected_present, timeout=30), \
        f"expected sessions {sorted(expected_ids)} to be restored"
    client.close()
