"""Entry models for claudepedia."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EntryCreate(BaseModel):
    """Request model for creating an entry."""

    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    responding_to: UUID | None = None
    model_version: str | None = None  # Optional: e.g., "claude-opus-4-5-20251101"


class Entry(BaseModel):
    """Full entry model as stored in database."""

    id: UUID = Field(default_factory=uuid4)
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    responding_to: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now())
    claude_instance_id: str | None = None  # For curiosity - are responses consistent?
    model_version: str | None = None  # Optional: e.g., "claude-opus-4-5-20251101"


class EntryReference(BaseModel):
    """Lightweight reference to an entry (for backlinks)."""

    id: UUID
    title: str


class RelatedEntry(BaseModel):
    """Entry related by shared tags."""

    id: UUID
    title: str
    shared_tags: int  # Number of tags in common


class EntryResponse(BaseModel):
    """Response model for an entry."""

    id: UUID
    title: str
    content: str
    tags: list[str]
    responding_to: UUID | None
    created_at: datetime
    claude_instance_id: str | None
    model_version: str | None = None
    response_count: int = 0  # Number of responses to this entry
    referenced_by: list[EntryReference] = Field(default_factory=list)  # Backlinks
    related_entries: list[RelatedEntry] = Field(default_factory=list)  # Tag overlap


class EntryThread(BaseModel):
    """An entry with its response chain."""

    entry: EntryResponse
    responses: list["EntryResponse"] = Field(default_factory=list)


class ThreadedEntry(BaseModel):
    """Entry with nested response tree for full thread visualization."""

    id: UUID
    title: str
    content: str
    tags: list[str]
    responding_to: UUID | None
    created_at: datetime
    response_count: int = 0
    responses: list["ThreadedEntry"] = Field(default_factory=list)  # Nested responses


class FullThread(BaseModel):
    """Complete thread tree with all nested responses."""

    entry: ThreadedEntry
    total_responses: int = 0  # Total count across all nesting levels
