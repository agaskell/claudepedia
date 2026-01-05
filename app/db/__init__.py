"""Database layer for claudepedia."""

from .database import get_db
from .repository import EntryRepository

__all__ = ["get_db", "EntryRepository"]
