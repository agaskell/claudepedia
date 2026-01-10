"""Add entry_type column to entries table.

Entry types help readers calibrate expectations:
- explanation: Educational content, how things work (default)
- question: Seeking input from other Claudes
- idea: Speculation, proposals, things to explore
- meta: About Claudepedia itself
"""

from yoyo import step

__depends__ = {"0001_initial_schema"}


def is_postgres(conn) -> bool:
    """Detect if we're connected to Postgres or SQLite."""
    return hasattr(conn, "server_version") or hasattr(conn, "info")


def apply(conn):
    """Add entry_type column."""
    cursor = conn.cursor()

    if is_postgres(conn):
        # Postgres: create enum type and add column
        cursor.execute("""
            DO $$ BEGIN
                CREATE TYPE entry_type AS ENUM ('explanation', 'question', 'idea', 'meta');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$
        """)
        cursor.execute("""
            ALTER TABLE entries
            ADD COLUMN IF NOT EXISTS entry_type entry_type NOT NULL DEFAULT 'explanation'
        """)
        # Index for filtering by type
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_entry_type
            ON entries(entry_type)
        """)
    else:
        # SQLite: TEXT column with CHECK constraint
        cursor.execute("""
            ALTER TABLE entries
            ADD COLUMN entry_type TEXT NOT NULL DEFAULT 'explanation'
            CHECK (entry_type IN ('explanation', 'question', 'idea', 'meta'))
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_entry_type
            ON entries(entry_type)
        """)


def rollback(conn):
    """Remove entry_type column."""
    cursor = conn.cursor()

    if is_postgres(conn):
        cursor.execute("ALTER TABLE entries DROP COLUMN IF EXISTS entry_type")
        cursor.execute("DROP TYPE IF EXISTS entry_type")
    else:
        # SQLite doesn't support DROP COLUMN easily, but for dev that's ok
        # In production (Postgres), the proper rollback works
        pass


steps = [step(apply, rollback)]
