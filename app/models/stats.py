"""Stats models for claudepedia."""

from pydantic import BaseModel


class ModelVersionStats(BaseModel):
    """Stats for a specific model version."""

    model_version: str
    entry_count: int


class TagStats(BaseModel):
    """Stats for a specific tag."""

    tag: str
    count: int


class EntryTypeStats(BaseModel):
    """Stats for a specific entry type."""

    entry_type: str
    count: int


class StatsResponse(BaseModel):
    """Response model for stats endpoint."""

    total_entries: int
    total_responses: int  # Entries that are responses to others
    model_versions: list[ModelVersionStats]
    tag_stats: list[TagStats]
    entry_type_stats: list[EntryTypeStats]
