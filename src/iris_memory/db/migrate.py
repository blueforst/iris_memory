"""Small deterministic SQLite migration runner for the repository bootstrap."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Summary of one migration run."""

    database_path: Path
    applied_versions: tuple[str, ...]


def _migration_files() -> tuple[Traversable, ...]:
    root = files("iris_memory.db.migrations")
    migrations = (entry for entry in root.iterdir() if entry.name.endswith(".sql"))
    return tuple(sorted(migrations, key=lambda entry: entry.name))


def apply_migrations(database_path: Path) -> MigrationResult:
    """Apply packaged SQL migrations exactly once, in filename order."""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )

        existing = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        }

        for migration in _migration_files():
            version = migration.name.removesuffix(".sql")
            if version in existing:
                continue
            sql = migration.read_text(encoding="utf-8")
            with connection:
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(UTC).isoformat()),
                )
            applied.append(version)

    return MigrationResult(database_path=database_path, applied_versions=tuple(applied))
