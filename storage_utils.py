"""Shared helpers for auth/storage filesystem checks."""

from pathlib import Path


def has_db_files(db_root: Path) -> bool:
    """Return True if any SQLite DB files exist under db_root."""
    return any(db_root.rglob("*.db"))
