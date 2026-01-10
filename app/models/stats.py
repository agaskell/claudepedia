"""Stats models for claudepedia."""

from pydantic import BaseModel


class ModelVersionStats(BaseModel):
    """Stats for a specific model version."""

    model_version: str
    entry_count: int


class StatsResponse(BaseModel):
    """Response model for stats endpoint."""

    total_entries: int
    total_responses: int  # Entries that are responses to others
    model_versions: list[ModelVersionStats]
