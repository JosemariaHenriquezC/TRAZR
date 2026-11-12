"""Fábrica del repositorio: una base SQLite en DATABASE_PATH."""
from ..config import Settings
from .base import Repository, StorageError
from .sqlite import SQLiteRepository


def build_repository(settings: Settings) -> Repository:
    return SQLiteRepository(settings.database_path)


__all__ = ["build_repository", "Repository", "StorageError", "SQLiteRepository"]
