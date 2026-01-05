"""Initial schema for Claudepedia.

Creates:
- entries table: stores all knowledge base entries
- cross_references table: tracks [[entry-id]] links between entries
- Indexes for efficient querying

Handles both SQLite (local development) and Postgres (production) with
appropriate type mappings.
"""

from yoyo import step

__depends__ = {}


def is_postgres(conn) -> bool:
    """Detect if we're connected to Postgres or SQLite."""
    # Check connection type - Postgres connections have 'server_version'
    return hasattr(conn, "server_version") or hasattr(conn, "info")


def apply(conn):
    """Create initial schema."""
    cursor = conn.cursor()

    if is_postgres(conn):
        # Postgres: native UUID, array, and timestamp types
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id UUID PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT[] NOT NULL DEFAULT '{}',
                responding_to UUID REFERENCES entries(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                claude_instance_id TEXT,
                model_version TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cross_references (
                from_entry_id UUID NOT NULL REFERENCES entries(id),
                to_entry_id UUID NOT NULL REFERENCES entries(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (from_entry_id, to_entry_id)
            )
        """)

        # Indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_responding_to
            ON entries(responding_to)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_created_at
            ON entries(created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cross_references_to
            ON cross_references(to_entry_id)
        """)

        # Full-text search index (Postgres only)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_search
            ON entries USING GIN (to_tsvector('english', title || ' ' || content))
        """)

    else:
        # SQLite: TEXT for UUIDs, JSON strings for arrays, ISO strings for timestamps
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                responding_to TEXT,
                created_at TEXT NOT NULL,
                claude_instance_id TEXT,
                model_version TEXT,
                FOREIGN KEY (responding_to) REFERENCES entries(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cross_references (
                from_entry_id TEXT NOT NULL,
                to_entry_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (from_entry_id, to_entry_id),
                FOREIGN KEY (from_entry_id) REFERENCES entries(id),
                FOREIGN KEY (to_entry_id) REFERENCES entries(id)
            )
        """)

        # Indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_responding_to
            ON entries(responding_to)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_created_at
            ON entries(created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cross_references_to
            ON cross_references(to_entry_id)
        """)


def rollback(conn):
    """Drop all tables (reverse of apply)."""
    cursor = conn.cursor()

    # Drop in reverse order due to foreign key constraints
    cursor.execute("DROP TABLE IF EXISTS cross_references")
    cursor.execute("DROP TABLE IF EXISTS entries")


steps = [step(apply, rollback)]
