"""Add accounts, verification codes, and API keys for posting auth.

Posting entries now requires an email-verified API key:
- accounts: one row per email address
- verification_codes: short-lived codes sent by email (stored hashed)
- api_keys: bearer keys issued after verification (stored hashed)
- entries.account_id: links new entries to the posting account

Reading stays public; only entry creation is authenticated.
"""

from yoyo import step

__depends__ = {"0002_add_entry_type"}


def is_postgres(conn) -> bool:
    """Detect if we're connected to Postgres or SQLite."""
    return hasattr(conn, "server_version") or hasattr(conn, "info")


def apply(conn):
    """Create auth tables and link entries to accounts."""
    cursor = conn.cursor()

    if is_postgres(conn):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id UUID PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                verified_at TIMESTAMPTZ
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS verification_codes (
                id UUID PRIMARY KEY,
                account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                code_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                used_at TIMESTAMPTZ,
                attempts INTEGER NOT NULL DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id UUID PRIMARY KEY,
                account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                key_hash TEXT NOT NULL UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                revoked_at TIMESTAMPTZ,
                last_used_at TIMESTAMPTZ
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_verification_codes_account
            ON verification_codes(account_id, created_at DESC)
        """)

        cursor.execute("""
            ALTER TABLE entries
            ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES accounts(id)
        """)

    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                verified_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS verification_codes (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                code_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                key_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                last_used_at TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_verification_codes_account
            ON verification_codes(account_id, created_at DESC)
        """)

        cursor.execute("""
            ALTER TABLE entries ADD COLUMN account_id TEXT REFERENCES accounts(id)
        """)


def rollback(conn):
    """Drop auth tables."""
    cursor = conn.cursor()

    if is_postgres(conn):
        cursor.execute("ALTER TABLE entries DROP COLUMN IF EXISTS account_id")
    # SQLite doesn't support DROP COLUMN easily; acceptable for local dev.
    cursor.execute("DROP TABLE IF EXISTS api_keys")
    cursor.execute("DROP TABLE IF EXISTS verification_codes")
    cursor.execute("DROP TABLE IF EXISTS accounts")


steps = [step(apply, rollback)]
