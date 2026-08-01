"""Small deterministic SQLite migration runner for the repository bootstrap."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Summary of one migration run."""

    database_path: Path
    applied_versions: tuple[str, ...]


def _migration_sources(migrations_dir: Path | None) -> tuple[tuple[str, str], ...]:
    if migrations_dir is not None:
        return tuple(
            sorted(
                (path.name, path.read_text(encoding="utf-8"))
                for path in migrations_dir.glob("*.sql")
            )
        )
    root = files("iris_memory.db.migrations")
    return tuple(
        sorted(
            (entry.name, entry.read_text(encoding="utf-8"))
            for entry in root.iterdir()
            if entry.name.endswith(".sql")
        )
    )


def apply_migrations(database_path: Path, *, migrations_dir: Path | None = None) -> MigrationResult:
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

        for migration_name, sql in _migration_sources(migrations_dir):
            version = migration_name.removesuffix(".sql")
            if version in existing:
                continue
            statements = [part.strip() for part in sql.split(";") if part.strip()]
            connection.execute("BEGIN")
            try:
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(UTC).isoformat()),
                )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise
            applied.append(version)

    return MigrationResult(database_path=database_path, applied_versions=tuple(applied))
