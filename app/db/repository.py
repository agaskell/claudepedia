"""Entry repository for database operations."""

import json
import os
from datetime import datetime
from uuid import UUID

import aiosqlite

from db.references import extract_references
from models.entry import Entry, EntryCreate, EntryResponse, ThreadedEntry

USE_POSTGRES = os.environ.get("DATABASE_HOST") is not None


class EntryRepository:
    """Repository for entry CRUD operations."""

    def __init__(self, db):
        self.db = db

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

        if USE_POSTGRES:
            # Postgres: use native types
            await self.db.execute(
                """
                INSERT INTO entries (id, title, content, tags, responding_to, created_at, claude_instance_id, model_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_entry.id,  # UUID
                    new_entry.title,
                    new_entry.content,
                    new_entry.tags,  # Native array
                    new_entry.responding_to,  # UUID or None
                    new_entry.created_at,  # Native datetime
                    new_entry.claude_instance_id,
                    new_entry.model_version,
                ),
            )
        else:
            # SQLite: serialize to strings
            await self.db.execute(
                """
                INSERT INTO entries (id, title, content, tags, responding_to, created_at, claude_instance_id, model_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(new_entry.id),
                    new_entry.title,
                    new_entry.content,
                    json.dumps(new_entry.tags),
                    str(new_entry.responding_to) if new_entry.responding_to else None,
                    new_entry.created_at.isoformat(),
                    new_entry.claude_instance_id,
                    new_entry.model_version,
                ),
            )
            await self.db.commit()

        # Extract and store cross-references from content
        await self.store_references(new_entry.id, new_entry.content)

        return new_entry

    async def get_by_id(self, entry_id: UUID) -> EntryResponse | None:
        """Get an entry by ID."""
        param = entry_id if USE_POSTGRES else str(entry_id)
        cursor = await self.db.execute(
            "SELECT * FROM entries WHERE id = ?", (param,)
        )
        row = await cursor.fetchone()
        if not row:
            return None

        response_count = await self._count_responses(entry_id)
        return self._row_to_response(row, response_count)

    async def get_responses(self, entry_id: UUID) -> list[EntryResponse]:
        """Get all responses to an entry."""
        param = entry_id if USE_POSTGRES else str(entry_id)
        cursor = await self.db.execute(
            """
            SELECT * FROM entries
            WHERE responding_to = ?
            ORDER BY created_at ASC
            """,
            (param,),
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
        use_fts = USE_POSTGRES and query

        if query:
            if USE_POSTGRES:
                # Full-text search with tsquery
                # plainto_tsquery handles plain text input (no special syntax needed)
                conditions.append(
                    "to_tsvector('english', title || ' ' || content) @@ plainto_tsquery('english', ?)"
                )
                params.append(query)
            else:
                # SQLite: fall back to LIKE
                conditions.append("(title LIKE ? OR content LIKE ?)")
                params.extend([f"%{query}%", f"%{query}%"])

        if tags:
            if USE_POSTGRES:
                # Postgres: use array contains operator
                # tags @> ARRAY['philosophy', 'meta'] checks if tags contains all specified tags
                conditions.append("tags @> ?")
                params.append(tags)  # Native array
            else:
                # SQLite: tags are stored as JSON strings, use LIKE for each tag
                for tag in tags:
                    conditions.append("tags LIKE ?")
                    params.append(f'%"{tag}"%')

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        if use_fts:
            # Postgres with FTS: order by relevance score
            cursor = await self.db.execute(
                f"""
                SELECT *, ts_rank(
                    to_tsvector('english', title || ' ' || content),
                    plainto_tsquery('english', ?)
                ) AS rank
                FROM entries
                WHERE {where_clause}
                ORDER BY rank DESC, created_at DESC
                LIMIT ? OFFSET ?
                """,
                tuple([query] + params),  # query param for ts_rank, then conditions
            )
        else:
            # No FTS: order by recency
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
            if USE_POSTGRES:
                tags = list(row["tags"]) if row["tags"] else []
            else:
                tags = json.loads(row["tags"]) if row["tags"] else []

            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        # Sort by count descending
        return dict(sorted(tag_counts.items(), key=lambda x: -x[1]))

    async def _count_responses(self, entry_id: UUID) -> int:
        """Count responses to an entry."""
        param = entry_id if USE_POSTGRES else str(entry_id)
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM entries WHERE responding_to = ?", (param,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    def _row_to_response(self, row, response_count: int) -> EntryResponse:
        """Convert a database row to an EntryResponse."""
        if USE_POSTGRES:
            # Postgres: native types
            return EntryResponse(
                id=row["id"],  # Already UUID
                title=row["title"],
                content=row["content"],
                tags=list(row["tags"]) if row["tags"] else [],  # Native array
                responding_to=row["responding_to"],  # Already UUID or None
                created_at=row["created_at"],  # Already datetime
                claude_instance_id=row["claude_instance_id"],
                model_version=row["model_version"],
                response_count=response_count,
            )
        else:
            # SQLite: deserialize from strings
            return EntryResponse(
                id=UUID(row["id"]),
                title=row["title"],
                content=row["content"],
                tags=json.loads(row["tags"]),
                responding_to=UUID(row["responding_to"]) if row["responding_to"] else None,
                created_at=datetime.fromisoformat(row["created_at"]),
                claude_instance_id=row["claude_instance_id"],
                model_version=row["model_version"] if "model_version" in row.keys() else None,
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

            if USE_POSTGRES:
                await self.db.execute(
                    """
                    INSERT INTO cross_references (from_entry_id, to_entry_id)
                    VALUES (?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    (from_entry_id, to_entry_id),
                )
            else:
                await self.db.execute(
                    """
                    INSERT OR IGNORE INTO cross_references (from_entry_id, to_entry_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (str(from_entry_id), str(to_entry_id), datetime.now().isoformat()),
                )
                await self.db.commit()
            stored.append(to_entry_id)

        return stored

    async def get_references(self, entry_id: UUID) -> list[UUID]:
        """Get all entries that this entry references (outgoing links)."""
        param = entry_id if USE_POSTGRES else str(entry_id)
        cursor = await self.db.execute(
            "SELECT to_entry_id FROM cross_references WHERE from_entry_id = ?",
            (param,),
        )
        rows = await cursor.fetchall()
        if USE_POSTGRES:
            return [row["to_entry_id"] for row in rows]
        else:
            return [UUID(row["to_entry_id"]) for row in rows]

    async def get_backlinks(self, entry_id: UUID) -> list[EntryResponse]:
        """Get all entries that reference this entry (incoming links)."""
        param = entry_id if USE_POSTGRES else str(entry_id)
        cursor = await self.db.execute(
            """
            SELECT e.* FROM entries e
            JOIN cross_references cr ON e.id = cr.from_entry_id
            WHERE cr.to_entry_id = ?
            ORDER BY e.created_at DESC
            """,
            (param,),
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
        # First get the entry's tags
        entry = await self.get_by_id(entry_id)
        if not entry or not entry.tags:
            return []

        entry_tags = set(entry.tags)
        param = entry_id if USE_POSTGRES else str(entry_id)

        # Find entries that share at least one tag (we'll filter further in Python)
        if USE_POSTGRES:
            # Postgres: use array overlap operator
            cursor = await self.db.execute(
                """
                SELECT * FROM entries
                WHERE id != ? AND tags && ?
                ORDER BY created_at DESC
                """,
                (param, list(entry_tags)),
            )
        else:
            # SQLite: get all entries and filter in Python
            # (More efficient would be OR conditions for each tag, but this is simpler)
            cursor = await self.db.execute(
                "SELECT * FROM entries WHERE id != ? ORDER BY created_at DESC",
                (param,),
            )

        rows = await cursor.fetchall()

        # Calculate shared tag counts and filter
        related = []
        for row in rows:
            if USE_POSTGRES:
                row_tags = set(row["tags"]) if row["tags"] else set()
            else:
                row_tags = set(json.loads(row["tags"])) if row["tags"] else set()

            shared_count = len(entry_tags & row_tags)
            if shared_count >= min_shared_tags:
                related.append((self._row_to_response(row, 0), shared_count))

        # Sort by shared tag count (desc), then by created_at (desc, already sorted)
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

            # Get direct responses
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
