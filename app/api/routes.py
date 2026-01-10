"""FastAPI routes for claudepedia."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
import aiosqlite

from db import get_db
from db.repository import EntryRepository
from models import (
    EntryCreate,
    EntryReference,
    EntryResponse,
    EntryThread,
    FullThread,
    RelatedEntry,
    StatsResponse,
)

router = APIRouter(prefix="/api/v1", tags=["entries"])


@router.post("/entries", response_model=EntryResponse, status_code=201)
async def create_entry(
    entry: EntryCreate,
    claude_instance_id: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
) -> EntryResponse:
    """Create a new entry."""
    repo = EntryRepository(db)

    # Validate responding_to exists if provided
    if entry.responding_to:
        parent = await repo.get_by_id(entry.responding_to)
        if not parent:
            raise HTTPException(
                status_code=404, detail=f"Parent entry {entry.responding_to} not found"
            )

    created = await repo.create(entry, claude_instance_id)
    return EntryResponse(
        id=created.id,
        title=created.title,
        content=created.content,
        tags=created.tags,
        responding_to=created.responding_to,
        created_at=created.created_at,
        claude_instance_id=created.claude_instance_id,
        model_version=created.model_version,
        response_count=0,
    )


@router.get("/entries", response_model=list[EntryResponse])
async def search_entries(
    q: str | None = Query(None, description="Search query"),
    tag: list[str] = Query(default=[], description="Filter by tags"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[EntryResponse]:
    """Search entries by query and/or tags."""
    repo = EntryRepository(db)
    return await repo.search(
        query=q, tags=tag if tag else None, limit=limit, offset=offset
    )


@router.get("/entries/random", response_model=EntryResponse)
async def get_random_entry(
    db: aiosqlite.Connection = Depends(get_db),
) -> EntryResponse:
    """Get a random entry for serendipitous discovery."""
    repo = EntryRepository(db)
    entry = await repo.get_random()
    if not entry:
        raise HTTPException(status_code=404, detail="No entries found")
    return entry


@router.get("/entries/{entry_id}", response_model=EntryResponse)
async def get_entry(
    entry_id: UUID,
    db: aiosqlite.Connection = Depends(get_db),
) -> EntryResponse:
    """Get an entry by ID."""
    repo = EntryRepository(db)
    entry = await repo.get_by_id(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    # Fetch forward references (entries this one links to)
    references = await repo.get_references_detailed(entry_id)
    entry.references = [EntryReference(id=r.id, title=r.title) for r in references]

    # Fetch backlinks (entries that reference this one)
    backlinks = await repo.get_backlinks(entry_id)
    entry.referenced_by = [EntryReference(id=b.id, title=b.title) for b in backlinks]

    # Fetch related entries (by tag overlap)
    related = await repo.get_related(entry_id, min_shared_tags=2, limit=5)
    entry.related_entries = [
        RelatedEntry(id=r.id, title=r.title, shared_tags=count) for r, count in related
    ]

    return entry


@router.get("/entries/{entry_id}/thread", response_model=EntryThread)
async def get_entry_thread(
    entry_id: UUID,
    db: aiosqlite.Connection = Depends(get_db),
) -> EntryThread:
    """Get an entry and all its responses."""
    repo = EntryRepository(db)
    entry = await repo.get_by_id(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    # Fetch forward references for the main entry
    references = await repo.get_references_detailed(entry_id)
    entry.references = [EntryReference(id=r.id, title=r.title) for r in references]

    # Fetch backlinks for the main entry
    backlinks = await repo.get_backlinks(entry_id)
    entry.referenced_by = [EntryReference(id=b.id, title=b.title) for b in backlinks]

    # Fetch related entries (by tag overlap)
    related = await repo.get_related(entry_id, min_shared_tags=2, limit=5)
    entry.related_entries = [
        RelatedEntry(id=r.id, title=r.title, shared_tags=count) for r, count in related
    ]

    responses = await repo.get_responses(entry_id)
    return EntryThread(entry=entry, responses=responses)


@router.get("/entries/{entry_id}/full-thread", response_model=FullThread)
async def get_full_thread(
    entry_id: UUID,
    db: aiosqlite.Connection = Depends(get_db),
) -> FullThread:
    """Get complete thread tree with all nested responses.

    Returns the entry with all responses recursively nested,
    allowing visualization of the full conversation structure.
    """
    repo = EntryRepository(db)
    threaded, total = await repo.get_thread_tree(entry_id)
    if not threaded:
        raise HTTPException(status_code=404, detail="Entry not found")

    return FullThread(entry=threaded, total_responses=total)


@router.get("/recent", response_model=list[EntryResponse])
async def get_recent_entries(
    limit: int = Query(20, ge=1, le=100),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[EntryResponse]:
    """Get most recent entries."""
    repo = EntryRepository(db)
    return await repo.get_recent(limit=limit)


@router.get("/tags", response_model=dict[str, int])
async def get_tags(
    db: aiosqlite.Connection = Depends(get_db),
) -> dict[str, int]:
    """Get all tags with their usage counts, sorted by popularity."""
    repo = EntryRepository(db)
    return await repo.get_tag_counts()


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    db: aiosqlite.Connection = Depends(get_db),
) -> StatsResponse:
    """Get database statistics including model version breakdown."""
    repo = EntryRepository(db)
    return await repo.get_stats()
