import sqlite3
from pathlib import Path

import pytest

from iris_memory.db import apply_migrations


def test_empty_database_initializes_and_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"

    first = apply_migrations(database_path)
    second = apply_migrations(database_path)

    assert first.applied_versions == ("0001_bootstrap", "0002_router_ledger")
    assert second.applied_versions == ()

    with sqlite3.connect(database_path) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        state = connection.execute(
            "SELECT value FROM service_metadata WHERE key = 'router_state'"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert versions == [("0001_bootstrap",), ("0002_router_ledger",)]
    assert state == ("ledger_initialized",)
    assert {
        "accepted_publications",
        "publication_idempotency",
        "acceptance_receipts",
        "evidence_envelopes",
        "ingestion_jobs",
        "service_metadata",
        "schema_migrations",
    } <= tables


def test_failed_migration_rolls_back_atomically(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_ok.sql").write_text(
        "CREATE TABLE ok_table (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    (migrations_dir / "0002_fail.sql").write_text(
        "CREATE TABLE partial_table (id INTEGER PRIMARY KEY); THIS IS NOT SQL;",
        encoding="utf-8",
    )

    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(database_path, migrations_dir=migrations_dir)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        versions = [
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]

    assert "ok_table" in tables
    assert "partial_table" not in tables
    assert versions == ["0001_ok"]
