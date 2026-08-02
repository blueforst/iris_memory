"""Small deterministic SQLite migration runner for the repository bootstrap.

Reliability contract (round 3):
- empty data root initializes through the full packaged migration chain;
- an applied migration whose SQL changed since it was recorded FAILS CLOSED
  (checksum mismatch), so a modified historical migration can never silently
  re-run or mask drift;
- migration failure is atomic: each migration runs inside one transaction and
  rolls back on error;
- the runner is idempotent across restarts (version + checksum recorded).
"""

from __future__ import annotations

import hashlib
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


class MigrationChecksumError(RuntimeError):
    """Raised when an already-applied migration changed since it was applied."""


def _sha256(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


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


def _ensure_checksum_column(connection: sqlite3.Connection) -> None:
    """Backfill the checksum column for databases migrated before round 3."""
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(schema_migrations)").fetchall()
    }
    if "checksum" not in columns:
        connection.execute(
            "ALTER TABLE schema_migrations ADD COLUMN checksum TEXT NOT NULL DEFAULT ''"
        )


def _recorded_migrations(connection: sqlite3.Connection) -> dict[str, str]:
    """Return {version: checksum} for already-applied migrations."""
    return {
        str(row[0]): str(row[1])
        for row in connection.execute(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
    }


def apply_migrations(database_path: Path, *, migrations_dir: Path | None = None) -> MigrationResult:
    """Apply packaged SQL migrations exactly once, in filename order.

    - An empty data root initializes through every packaged migration.
    - A migration already recorded with a different checksum FAILS CLOSED.
    - Each migration commits atomically (BEGIN/COMMIT around all statements
      + the bookkeeping insert).
    - After applying, every recorded version is re-verified against the
      on-disk SQL so a historical modification cannot pass silently.
    """
    database_path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL,
                checksum TEXT NOT NULL DEFAULT ''
            )
            """
        )
        _ensure_checksum_column(connection)
        recorded = _recorded_migrations(connection)

        # MAJOR#2 (review): pre-round-3 rows have checksum ''. Backfill the
        # current checksum (a documented blessing of unknown history) so
        # future tampering is detected. Runs OUTSIDE the per-migration BEGIN
        # (python sqlite3 autocommits this standalone UPDATE).
        sources = _migration_sources(migrations_dir)
        source_checksums = {name.removesuffix(".sql"): _sha256(sql) for name, sql in sources}
        backfilled_any = False
        for version, recorded_checksum in recorded.items():
            current_checksum = source_checksums.get(version)
            if recorded_checksum == "" and current_checksum is not None:
                connection.execute(
                    "UPDATE schema_migrations SET checksum = ? WHERE version = ?",
                    (current_checksum, version),
                )
                recorded[version] = current_checksum
                backfilled_any = True
        # Commit the standalone backfill so the per-migration BEGIN below
        # never collides with python sqlite3's implicit transaction.
        if backfilled_any:
            connection.commit()

        for migration_name, sql in sources:
            version = migration_name.removesuffix(".sql")
            checksum = _sha256(sql)
            if version in recorded:
                # Empty checksums were backfilled in the pass above; any
                # remaining non-empty mismatch is a real drift.
                recorded_checksum = recorded[version]
                if recorded_checksum != checksum:
                    raise MigrationChecksumError(
                        f"migration {version} changed after being applied "
                        f"(recorded {recorded_checksum}, current {checksum})"
                    )
                continue

            statements = [part.strip() for part in sql.split(";") if part.strip()]
            connection.execute("BEGIN")
            try:
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at, checksum) VALUES (?, ?, ?)",
                    (version, datetime.now(UTC).isoformat(), checksum),
                )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise
            applied.append(version)
            recorded[version] = checksum

    return MigrationResult(database_path=database_path, applied_versions=tuple(applied))
