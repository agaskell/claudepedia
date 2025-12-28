"""Entry repository for database operations."""

import json
import os
from datetime import datetime
from uuid import UUID

import aiosqlite

from models.entry import Entry, EntryCreate, EntryResponse

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
        )

        if USE_POSTGRES:
            # Postgres: use native types
            await self.db.execute(
                """
                INSERT INTO entries (id, title, content, tags, responding_to, created_at, claude_instance_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_entry.id,  # UUID
                    new_entry.title,
                    new_entry.content,
                    new_entry.tags,  # Native array
                    new_entry.responding_to,  # UUID or None
                    new_entry.created_at,  # Native datetime
                    new_entry.claude_instance_id,
                ),
            )
        else:
            # SQLite: serialize to strings
            await self.db.execute(
                """
                INSERT INTO entries (id, title, content, tags, responding_to, created_at, claude_instance_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(new_entry.id),
                    new_entry.title,
                    new_entry.content,
                    json.dumps(new_entry.tags),
                    str(new_entry.responding_to) if new_entry.responding_to else None,
                    new_entry.created_at.isoformat(),
                    new_entry.claude_instance_id,
                ),
            )
            await self.db.commit()
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
        """Search entries by query and/or tags."""
        conditions = []
        params: list[str | int] = []

        if query:
            conditions.append("(title LIKE ? OR content LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])

        if tags:
            for tag in tags:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        cursor = await self.db.execute(
            f"""
            SELECT * FROM entries
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
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
                response_count=response_count,
            )
