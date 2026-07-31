import sqlite3
from pathlib import Path

from iris_memory.db import apply_migrations


def test_empty_database_initializes_and_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"

    first = apply_migrations(database_path)
    second = apply_migrations(database_path)

    assert first.applied_versions == ("0001_bootstrap",)
    assert second.applied_versions == ()

    with sqlite3.connect(database_path) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        state = connection.execute(
            "SELECT value FROM service_metadata WHERE key = 'repository_state'"
        ).fetchone()

    assert versions == [("0001_bootstrap",)]
    assert state == ("bootstrap",)
