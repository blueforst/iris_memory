import sqlite3
from pathlib import Path

import pytest

from iris_memory.acceptance import (
    Accepted,
    DuplicateReplay,
    IdempotencyConflict,
    accept_publication,
)
from iris_memory.db import apply_migrations
from iris_memory.db.migrate import MigrationChecksumError


def test_empty_database_initializes_and_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"

    first = apply_migrations(database_path)
    second = apply_migrations(database_path)

    assert first.applied_versions == (
        "0001_bootstrap",
        "0002_router_ledger",
        "0003_router_idempotency_rebuild",
        "0004_checksum_metadata",
    )
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

    assert versions == [
        ("0001_bootstrap",),
        ("0002_router_ledger",),
        ("0003_router_idempotency_rebuild",),
        ("0004_checksum_metadata",),
    ]
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


def test_old_0002_schema_upgrades_to_0003_and_consumes_alternate_key(
    tmp_path: Path,
) -> None:
    """Simulate a data root that already applied the original 0002 (which had
    publication_id UNIQUE), then upgrade with the current migration set and
    verify alternate-key replay consumes the key on the rebuilt table."""
    database_path = tmp_path / "router.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES "
            "('0001_bootstrap', '2026-08-01T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES "
            "('0002_router_ledger', '2026-08-01T00:00:00Z')"
        )
        # Original 0002 schema: publication_id has a UNIQUE constraint.
        connection.execute(
            "CREATE TABLE accepted_publications ("
            "publication_id TEXT PRIMARY KEY, contract_version TEXT NOT NULL, "
            "source_sequence INTEGER NOT NULL UNIQUE, canonical_payload_hash TEXT NOT NULL, "
            "receipt_id TEXT NOT NULL UNIQUE, accepted_at TEXT NOT NULL, "
            "payload_json TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE publication_idempotency ("
            "idempotency_key TEXT PRIMARY KEY, publication_id TEXT NOT NULL UNIQUE, "
            "canonical_payload_hash TEXT NOT NULL, accepted_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE acceptance_receipts ("
            "receipt_id TEXT PRIMARY KEY, publication_id TEXT NOT NULL UNIQUE, "
            "status TEXT NOT NULL, receipt_json TEXT NOT NULL, accepted_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE evidence_envelopes ("
            "envelope_id TEXT PRIMARY KEY, publication_id TEXT NOT NULL UNIQUE, "
            "contract_version TEXT NOT NULL, source_sequence INTEGER NOT NULL, "
            "envelope_json TEXT NOT NULL, accepted_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE ingestion_jobs ("
            "job_id TEXT PRIMARY KEY, publication_id TEXT NOT NULL UNIQUE, "
            "source_sequence INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
            "graphiti_status TEXT NOT NULL DEFAULT 'not_configured', "
            "attempt_count INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE service_metadata ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO service_metadata(key, value, updated_at) VALUES "
            "('router_state', 'ledger_initialized', '2026-08-01T00:00:00Z')"
        )

    # Upgrade: 0003 must rebuild publication_idempotency without UNIQUE, and
    # 0004 adds the checksum column + backfills 0001/0002 from the release
    # manifest.
    result = apply_migrations(database_path)
    assert result.applied_versions == (
        "0003_router_idempotency_rebuild",
        "0004_checksum_metadata",
    )

    with sqlite3.connect(database_path) as connection:
        index_info = connection.execute("PRAGMA index_list(publication_idempotency)").fetchall()
        # The rebuilt table must allow multiple keys per publication.
        connection.execute(
            "INSERT INTO publication_idempotency"
            "(idempotency_key, publication_id, canonical_payload_hash, accepted_at) "
            "VALUES ('k1', 'p1', 'hash1', 't')"
        )
        connection.execute(
            "INSERT INTO publication_idempotency"
            "(idempotency_key, publication_id, canonical_payload_hash, accepted_at) "
            "VALUES ('k2', 'p1', 'hash1', 't')"
        )
        assert len(index_info) >= 1  # publication index present
        # Clean the manual rows so the end-to-end path below starts fresh.
        connection.execute("DELETE FROM publication_idempotency")

    # End-to-end: alternate-key replay consumes the key on the upgraded root.
    request = {
        "schemaVersion": "publication-acceptance-request-v1",
        "contractVersion": "0.1.0",
        "idempotencyKey": "k1",
        "publication": {
            "schemaVersion": "historian-publication-v1",
            "publicationId": "11111111-1111-4111-8111-111111111111",
            "sourceSequence": 1,
            "publishedAt": "2026-08-01T00:00:00Z",
            "payloadHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "compartmentCount": 1,
            "segmentCount": 1,
            "evidenceCount": 1,
            "summary": "upgrade fixture",
        },
    }
    first = accept_publication(database_path, request)
    assert isinstance(first, Accepted)
    replay = accept_publication(
        database_path,
        {**request, "idempotencyKey": "k2"},
    )
    assert isinstance(replay, DuplicateReplay)
    later = accept_publication(
        database_path,
        {
            **request,
            "idempotencyKey": "k2",
            "publication": {
                **request["publication"],
                "publicationId": "22222222-2222-4222-8222-222222222222",
                "sourceSequence": 2,
                "summary": "different publication reusing consumed key",
            },
        },
    )
    assert isinstance(later, IdempotencyConflict)


def test_migration_checksum_fails_closed_when_applied_migration_changes(
    tmp_path: Path,
) -> None:
    """A migration modified after being applied must fail closed — the runner
    must never silently re-run or mask drift."""
    data_root = tmp_path / "memory"
    database_path = data_root / "router.sqlite3"
    migrations_dir = data_root / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "0001_first.sql").write_text(
        "CREATE TABLE t (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    result = apply_migrations(database_path, migrations_dir=migrations_dir)
    assert result.applied_versions == ("0001_first",)

    # Now modify the already-applied migration's SQL: must fail closed.
    (migrations_dir / "0001_first.sql").write_text(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, extra TEXT);",
        encoding="utf-8",
    )
    with pytest.raises(MigrationChecksumError, match="0001_first changed after being applied"):
        apply_migrations(database_path, migrations_dir=migrations_dir)


def test_migration_failure_rolls_back_atomically(tmp_path: Path) -> None:
    """A failing migration must leave no partial schema — the transaction
    rolls back the statements AND the bookkeeping insert."""
    data_root = tmp_path / "memory"
    database_path = data_root / "router.sqlite3"
    migrations_dir = data_root / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "0001_bad.sql").write_text(
        "CREATE TABLE ok_table (id INTEGER);\nCREATE TABLE broken (id INTEGER NOT);",
        encoding="utf-8",
    )

    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(database_path, migrations_dir=migrations_dir)

    # The whole migration (including the valid first statement) rolled back.
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "ok_table" not in tables, "valid statements before the failure must roll back"
    assert "schema_migrations" not in tables or "0001_bad" not in {
        row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
    }


def test_migration_reapply_is_idempotent_across_restart(tmp_path: Path) -> None:
    """Restarting the runner after a successful apply is a no-op and keeps the
    checksum records intact."""
    data_root = tmp_path / "memory"
    database_path = data_root / "router.sqlite3"
    migrations_dir = data_root / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "0001_first.sql").write_text(
        "CREATE TABLE t (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    apply_migrations(database_path, migrations_dir=migrations_dir)
    second = apply_migrations(database_path, migrations_dir=migrations_dir)
    assert second.applied_versions == ()
    with sqlite3.connect(database_path) as connection:
        checksum = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE version='0001_first'"
        ).fetchone()
    assert checksum is not None and checksum[0] != ""


def _sql_sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_legacy_empty_checksum_is_backfilled_from_release_manifest(tmp_path: Path) -> None:
    """M4 (review): a pre-round-3 row with checksum '' is backfilled ONLY from
    the release-owned checksums manifest — never from current disk bytes.
    After backfill, tampering of the applied migration IS detected."""
    data_root = tmp_path / "memory"
    database_path = data_root / "router.sqlite3"
    migrations_dir = data_root / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "0001_first.sql").write_text(
        "CREATE TABLE t (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    apply_migrations(database_path, migrations_dir=migrations_dir)
    original_sha = _sql_sha256((migrations_dir / "0001_first.sql").read_text(encoding="utf-8"))

    # Simulate a legacy row: blank the checksum.
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = '' WHERE version = '0001_first'"
        )
    # The release-owned manifest is the ONLY trusted source for the backfill.
    (migrations_dir / "checksums.json").write_text(
        '{"checksums": {"0001_first": "' + original_sha + '"}}',
        encoding="utf-8",
    )

    # Next run backfills the REAL release-owned checksum.
    apply_migrations(database_path, migrations_dir=migrations_dir)
    with sqlite3.connect(database_path) as connection:
        checksum = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE version='0001_first'"
        ).fetchone()
    assert checksum is not None and checksum[0] == original_sha

    # Tampering the applied migration IS now detected (fail-closed restored).
    (migrations_dir / "0001_first.sql").write_text(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, extra TEXT);",
        encoding="utf-8",
    )
    with pytest.raises(MigrationChecksumError):
        apply_migrations(database_path, migrations_dir=migrations_dir)


def test_legacy_empty_checksum_without_release_manifest_fails_closed(tmp_path: Path) -> None:
    """M4 (review): a legacy empty checksum with NO release-owned manifest
    entry must fail closed (explicit audited migration required), never
    silently blessed from current disk bytes."""
    data_root = tmp_path / "memory"
    database_path = data_root / "router.sqlite3"
    migrations_dir = data_root / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "0001_first.sql").write_text(
        "CREATE TABLE t (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    apply_migrations(database_path, migrations_dir=migrations_dir)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = '' WHERE version = '0001_first'"
        )
    # No checksums.json provided: backfill must fail closed.
    with pytest.raises(MigrationChecksumError, match="no release-owned checksum"):
        apply_migrations(database_path, migrations_dir=migrations_dir)
