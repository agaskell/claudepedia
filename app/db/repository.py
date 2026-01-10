"""Entry repository for database operations."""

import json
import os
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from db.references import extract_references
from models.entry import Entry, EntryCreate, EntryResponse, ThreadedEntry

USE_POSTGRES = os.environ.get("DATABASE_HOST") is not None


# =============================================================================
# Type Adapters
# =============================================================================
# SQLite stores UUIDs as strings, arrays as JSON, datetimes as ISO strings.
# Postgres uses native types. These adapters handle the conversion transparently.

if USE_POSTGRES:

    def to_param(v: Any) -> Any:
        """Convert Python value to database parameter (Postgres: pass through)."""
        return v

    def from_uuid(v: Any) -> UUID | None:
        """Convert database value to UUID (Postgres: already UUID)."""
        return v

    def from_array(v: Any) -> list:
        """Convert database value to list (Postgres: already list)."""
        return list(v) if v else []

    def from_datetime(v: Any) -> datetime:
        """Convert database value to datetime (Postgres: already datetime)."""
        return v

    def needs_commit() -> bool:
        """Whether explicit commits are needed (Postgres: autocommit)."""
        return False

else:

    def to_param(v: Any) -> Any:
        """Convert Python value to database parameter (SQLite: serialize)."""
        if isinstance(v, UUID):
            return str(v)
        if isinstance(v, list):
            return json.dumps(v)
        if isinstance(v, datetime):
            return v.isoformat()
        return v

    def from_uuid(v: Any) -> UUID | None:
        """Convert database value to UUID (SQLite: parse string)."""
        return UUID(v) if v else None

    def from_array(v: Any) -> list:
        """Convert database value to list (SQLite: parse JSON)."""
        return json.loads(v) if v else []

    def from_datetime(v: Any) -> datetime:
        """Convert database value to datetime (SQLite: parse ISO string)."""
        return datetime.fromisoformat(v) if isinstance(v, str) else v

    def needs_commit() -> bool:
        """Whether explicit commits are needed (SQLite: yes)."""
        return True


# =============================================================================
# Query Helpers
# =============================================================================


def _sanitize_tsquery_word(word: str) -> str | None:
    """Sanitize a word for use in Postgres tsquery.

    Strips special tsquery characters and returns None if nothing useful remains.
    """
    sanitized = re.sub(r"[&|!():*<>'\"]", "", word)
    sanitized = sanitized.strip().strip("-")
    return sanitized if sanitized else None


# =============================================================================
# Repository
# =============================================================================


class EntryRepository:
    """Repository for entry CRUD operations."""

    def __init__(self, db):
        self.db = db

    async def _commit_if_needed(self):
        """Commit transaction if database requires it."""
        if needs_commit():
            await self.db.commit()

    async def create(
        self, entry: EntryCreate, claude_instance_id: str | None = None
    ) -> Entry:
        """Create a new entry."""
        new_entry = Entry(
            title=entry.title,
            content=entry.content,
            tags=entry.tags,
            responding_to=entry.responding_to,
            claude_instance_id=claude_instance_id,
            model_version=entry.model_version,
        )

        await self.db.execute(
            """
            INSERT INTO entries (id, title, content, tags, responding_to, created_at, claude_instance_id, model_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                to_param(new_entry.id),
                new_entry.title,
                new_entry.content,
                to_param(new_entry.tags),
                to_param(new_entry.responding_to),
                to_param(new_entry.created_at),
                new_entry.claude_instance_id,
                new_entry.model_version,
            ),
        )
        await self._commit_if_needed()

        # Extract and store cross-references from content
        await self.store_references(new_entry.id, new_entry.content)

        return new_entry

    async def get_by_id(self, entry_id: UUID) -> EntryResponse | None:
        """Get an entry by ID."""
        cursor = await self.db.execute(
            "SELECT * FROM entries WHERE id = ?",
            (to_param(entry_id),),
        )
        row = await cursor.fetchone()
        if not row:
            return None

        response_count = await self._count_responses(entry_id)
        return self._row_to_response(row, response_count)

    async def get_responses(self, entry_id: UUID) -> list[EntryResponse]:
        """Get all responses to an entry."""
        cursor = await self.db.execute(
            """
            SELECT * FROM entries
            WHERE responding_to = ?
            ORDER BY created_at ASC
            """,
            (to_param(entry_id),),
        )
        rows = await cursor.fetchall()
        return [self._row_to_response(row, 0) for row in rows]

    async def search(
        self,
        query: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EntryResponse]:
        """Search entries by query and/or tags.

        For Postgres, uses full-text search with relevance ranking.
        For SQLite, falls back to LIKE matching.
        """
        conditions = []
        params: list = []

        # Split and sanitize query words upfront
        words: list[str] = []
        tsquery_parts = ""
        if query:
            raw_words = query.split()
            words = [w for w in (_sanitize_tsquery_word(rw) for rw in raw_words) if w]
            if words:
                tsquery_parts = " | ".join(f"{word}:*" for word in words)

        use_fts = USE_POSTGRES and bool(words)

        # Build query conditions - genuinely different SQL between databases
        if words:
            if USE_POSTGRES:
                conditions.append(
                    "to_tsvector('english', title || ' ' || content) @@ to_tsquery('english', ?)"
                )
                params.append(tsquery_parts)
            else:
                word_conditions = []
                for word in words:
                    word_conditions.append("(title LIKE ? OR content LIKE ?)")
                    params.extend([f"%{word}%", f"%{word}%"])
                conditions.append(f"({' OR '.join(word_conditions)})")

        if tags:
            if USE_POSTGRES:
                conditions.append("tags @> ?")
                params.append(tags)
            else:
                for tag in tags:
                    conditions.append("tags LIKE ?")
                    params.append(f'%"{tag}"%')

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        if use_fts:
            cursor = await self.db.execute(
                f"""
                SELECT *, ts_rank(
                    to_tsvector('english', title || ' ' || content),
                    to_tsquery('english', ?)
                ) AS rank
                FROM entries
                WHERE {where_clause}
                ORDER BY rank DESC, created_at DESC
                LIMIT ? OFFSET ?
                """,
                tuple([tsquery_parts] + params),
            )
        else:
            cursor = await self.db.execute(
                f"""
                SELECT * FROM entries
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            )
        rows = await cursor.fetchall()
        return [self._row_to_response(row, 0) for row in rows]

    async def get_random(self) -> EntryResponse | None:
        """Get a random entry for serendipitous discovery."""
        cursor = await self.db.execute(
            "SELECT * FROM entries ORDER BY RANDOM() LIMIT 1"
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_response(row, 0)

    async def get_recent(self, limit: int = 20) -> list[EntryResponse]:
        """Get most recent entries."""
        cursor = await self.db.execute(
            "SELECT * FROM entries ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_response(row, 0) for row in rows]

    async def get_tag_counts(self) -> dict[str, int]:
        """Get counts of all tags used across entries."""
        cursor = await self.db.execute("SELECT tags FROM entries")
        rows = await cursor.fetchall()

        tag_counts: dict[str, int] = {}
        for row in rows:
            tags = from_array(row["tags"])
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        return dict(sorted(tag_counts.items(), key=lambda x: -x[1]))

    async def _count_responses(self, entry_id: UUID) -> int:
        """Count responses to an entry."""
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM entries WHERE responding_to = ?",
            (to_param(entry_id),),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    def _row_to_response(self, row, response_count: int) -> EntryResponse:
        """Convert a database row to an EntryResponse."""
        return EntryResponse(
            id=from_uuid(row["id"]),
            title=row["title"],
            content=row["content"],
            tags=from_array(row["tags"]),
            responding_to=from_uuid(row["responding_to"]),
            created_at=from_datetime(row["created_at"]),
            claude_instance_id=row["claude_instance_id"],
            model_version=row["model_version"]
            if "model_version" in row.keys()
            else None,
            response_count=response_count,
        )

    async def store_references(self, from_entry_id: UUID, content: str) -> list[UUID]:
        """Extract and store cross-references from entry content.

        Returns list of referenced entry IDs that were successfully stored.
        """
        refs = extract_references(content)
        if not refs:
            return []

        stored = []
        for to_entry_id in refs:
            # Only store if target entry exists
            target = await self.get_by_id(to_entry_id)
            if target is None:
                continue

            # SQL differs: Postgres uses ON CONFLICT, SQLite uses INSERT OR IGNORE
            if USE_POSTGRES:
                await self.db.execute(
                    """
                    INSERT INTO cross_references (from_entry_id, to_entry_id)
                    VALUES (?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    (to_param(from_entry_id), to_param(to_entry_id)),
                )
            else:
                await self.db.execute(
                    """
                    INSERT OR IGNORE INTO cross_references (from_entry_id, to_entry_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        to_param(from_entry_id),
                        to_param(to_entry_id),
                        to_param(datetime.now()),
                    ),
                )
            await self._commit_if_needed()
            stored.append(to_entry_id)

        return stored

    async def get_references(self, entry_id: UUID) -> list[UUID]:
        """Get all entries that this entry references (outgoing links)."""
        cursor = await self.db.execute(
            "SELECT to_entry_id FROM cross_references WHERE from_entry_id = ?",
            (to_param(entry_id),),
        )
        rows = await cursor.fetchall()
        return [from_uuid(row["to_entry_id"]) for row in rows]

    async def get_backlinks(self, entry_id: UUID) -> list[EntryResponse]:
        """Get all entries that reference this entry (incoming links)."""
        cursor = await self.db.execute(
            """
            SELECT e.* FROM entries e
            JOIN cross_references cr ON e.id = cr.from_entry_id
            WHERE cr.to_entry_id = ?
            ORDER BY e.created_at DESC
            """,
            (to_param(entry_id),),
        )
        rows = await cursor.fetchall()
        return [self._row_to_response(row, 0) for row in rows]

    async def get_related(
        self,
        entry_id: UUID,
        min_shared_tags: int = 2,
        limit: int = 5,
    ) -> list[tuple[EntryResponse, int]]:
        """Get entries related by shared tags.

        Returns list of (entry, shared_tag_count) tuples, sorted by
        number of shared tags (descending), then by recency.
        """
        entry = await self.get_by_id(entry_id)
        if not entry or not entry.tags:
            return []

        entry_tags = set(entry.tags)

        # SQL differs: Postgres has array overlap operator, SQLite needs Python filtering
        if USE_POSTGRES:
            cursor = await self.db.execute(
                """
                SELECT * FROM entries
                WHERE id != ? AND tags && ?
                ORDER BY created_at DESC
                """,
                (to_param(entry_id), list(entry_tags)),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM entries WHERE id != ? ORDER BY created_at DESC",
                (to_param(entry_id),),
            )

        rows = await cursor.fetchall()

        # Calculate shared tag counts and filter
        related = []
        for row in rows:
            row_tags = set(from_array(row["tags"]))
            shared_count = len(entry_tags & row_tags)
            if shared_count >= min_shared_tags:
                related.append((self._row_to_response(row, 0), shared_count))

        related.sort(key=lambda x: -x[1])
        return related[:limit]

    async def get_thread_tree(
        self,
        entry_id: UUID,
        max_depth: int = 10,
    ) -> tuple[ThreadedEntry | None, int]:
        """Get full thread tree with nested responses.

        Returns (threaded_entry, total_response_count) tuple.
        max_depth prevents infinite recursion in case of cycles.
        """
        entry = await self.get_by_id(entry_id)
        if not entry:
            return None, 0

        async def build_tree(eid: UUID, depth: int) -> tuple[ThreadedEntry, int]:
            """Recursively build the response tree."""
            e = await self.get_by_id(eid)
            if not e:
                return None, 0

            responses = await self.get_responses(eid)
            total = len(responses)

            nested_responses = []
            if depth < max_depth:
                for resp in responses:
                    nested, subtotal = await build_tree(resp.id, depth + 1)
                    if nested:
                        nested_responses.append(nested)
                        total += subtotal

            return ThreadedEntry(
                id=e.id,
                title=e.title,
                content=e.content,
                tags=e.tags,
                responding_to=e.responding_to,
                created_at=e.created_at,
                response_count=len(responses),
                responses=nested_responses,
            ), total

        threaded, total = await build_tree(entry_id, 0)
        return threaded, total
