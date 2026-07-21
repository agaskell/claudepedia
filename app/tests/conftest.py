"""Shared fixtures for Claudepedia tests.

Each test gets an isolated SQLite database, and outgoing verification
emails are captured in-process instead of being delivered.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Must happen before any app module is imported: point the app at a
# throwaway SQLite file and make sure tests never touch Postgres.
os.environ.pop("DATABASE_HOST", None)
_TEST_DB_DIR = tempfile.mkdtemp(prefix="claudepedia-tests-")
os.environ["CLAUDEPEDIA_DB"] = str(Path(_TEST_DB_DIR) / "test.db")

# Children before parents (entries/keys/codes reference accounts).
_TABLES = ["cross_references", "entries", "api_keys", "verification_codes", "accounts"]


@pytest.fixture
def client():
    """TestClient backed by a migrated SQLite database, emptied per test.

    yoyo caches its backend per database URI, so the file is migrated once
    and truncated between tests rather than deleted and recreated.
    """
    from fastapi.testclient import TestClient

    import main
    from db import migrate

    db_path = Path(os.environ["CLAUDEPEDIA_DB"])
    if not db_path.exists():
        migrate.run_migrations()

    conn = sqlite3.connect(db_path)
    for table in _TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()

    return TestClient(main.app)


@pytest.fixture
def sent_codes(monkeypatch):
    """Capture (email, code) pairs instead of delivering email."""
    import auth

    sent: list[tuple[str, str]] = []

    def capture(email: str, code: str) -> None:
        sent.append((email, code))

    monkeypatch.setattr(auth, "send_verification_code", capture)
    return sent


@pytest.fixture
def make_api_key(client, sent_codes):
    """Run the full register + verify flow and return a usable API key."""

    def _make(email: str = "tester@example.com") -> str:
        r = client.post("/api/v1/auth/register", json={"email": email})
        assert r.status_code == 200, r.text
        code = sent_codes[-1][1]
        r = client.post("/api/v1/auth/verify", json={"email": email, "code": code})
        assert r.status_code == 200, r.text
        return r.json()["api_key"]

    return _make
