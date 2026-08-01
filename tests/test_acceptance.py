import sqlite3
from pathlib import Path
from typing import Any

import pytest

from iris_memory.acceptance import (
    Accepted,
    DuplicateReplay,
    IdempotencyConflict,
    SequenceConflict,
    UnsupportedVersion,
    ValidationFailure,
    accept_publication,
)
from iris_memory.contracts.validation import validate_instance
from iris_memory.db import apply_migrations
from iris_memory.health import build_health_report

PUBLICATION_ID = "11111111-1111-4111-8111-111111111111"
HASH_64 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _publication(
    *,
    publication_id: str = PUBLICATION_ID,
    source_sequence: int = 1,
    summary: str = "first publication",
) -> dict[str, Any]:
    return {
        "schemaVersion": "historian-publication-v1",
        "publicationId": publication_id,
        "sourceSequence": source_sequence,
        "publishedAt": "2026-08-01T00:00:00Z",
        "payloadHash": HASH_64,
        "compartmentCount": 1,
        "segmentCount": 1,
        "evidenceCount": 1,
        "summary": summary,
    }


def _request(
    *,
    idempotency_key: str = "agent-run-001",
    contract_version: str = "0.1.0",
    publication: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": "publication-acceptance-request-v1",
        "contractVersion": contract_version,
        "idempotencyKey": idempotency_key,
        "publication": publication if publication is not None else _publication(),
    }


def _counts(database_path: Path) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "accepted_publications",
                "publication_idempotency",
                "acceptance_receipts",
                "ingestion_jobs",
            )
        }


def test_first_acceptance_returns_valid_deterministic_receipt(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"

    outcome = accept_publication(database_path, _request())

    assert isinstance(outcome, Accepted)
    valid, errors = validate_instance("acceptance-receipt-v1.schema.json", outcome.receipt)
    assert valid, errors
    assert outcome.receipt["publicationId"] == PUBLICATION_ID
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 1,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_exact_replay_returns_duplicate_receipt_with_same_id(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    request = _request()

    first = accept_publication(database_path, request)
    second = accept_publication(database_path, request)

    assert isinstance(first, Accepted)
    assert isinstance(second, DuplicateReplay)
    assert second.receipt["receiptId"] == first.receipt["receiptId"]
    assert second.receipt["canonicalPayloadHash"] == first.receipt["canonicalPayloadHash"]
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 1,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_same_idempotency_different_payload_conflicts(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"

    first = accept_publication(database_path, _request())
    changed = _request(
        idempotency_key="agent-run-001",
        publication=_publication(
            publication_id="22222222-2222-4222-8222-222222222222",
            source_sequence=2,
            summary="different payload",
        ),
    )
    conflict = accept_publication(database_path, changed)

    assert isinstance(first, Accepted)
    assert isinstance(conflict, IdempotencyConflict)
    assert conflict.error["expectedCanonicalPayloadHash"] == first.receipt["canonicalPayloadHash"]
    assert conflict.error["receivedCanonicalPayloadHash"] != first.receipt["canonicalPayloadHash"]
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 1,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_source_sequence_conflict_returns_typed_error(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"

    first = accept_publication(database_path, _request())
    second = accept_publication(
        database_path,
        _request(
            idempotency_key="agent-run-002",
            publication=_publication(
                publication_id="22222222-2222-4222-8222-222222222222",
                source_sequence=1,
                summary="same sequence, different publication",
            ),
        ),
    )

    assert isinstance(first, Accepted)
    assert isinstance(second, SequenceConflict)
    assert second.error["sourceSequence"] == 1
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 2,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_same_publication_different_payload_uses_stored_idempotency_key(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "router.sqlite3"

    first = accept_publication(database_path, _request(idempotency_key="agent-original"))
    conflict = accept_publication(
        database_path,
        _request(
            idempotency_key="agent-second",
            publication=_publication(summary="different payload for same publication"),
        ),
    )

    assert isinstance(first, Accepted)
    assert isinstance(conflict, IdempotencyConflict)
    assert conflict.error["idempotencyKey"] == "agent-original"
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 2,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_alternate_key_replay_is_consumed(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    first = accept_publication(database_path, _request(idempotency_key="agent-original"))
    replay = accept_publication(database_path, _request(idempotency_key="agent-alternate"))

    assert isinstance(first, Accepted)
    assert isinstance(replay, DuplicateReplay)
    assert replay.receipt["receiptId"] == first.receipt["receiptId"]

    later = accept_publication(
        database_path,
        _request(
            idempotency_key="agent-alternate",
            publication=_publication(
                publication_id="22222222-2222-4222-8222-222222222222",
                source_sequence=2,
                summary="different publication reusing consumed key",
            ),
        ),
    )

    assert isinstance(later, IdempotencyConflict)
    assert later.error["publicationId"] == PUBLICATION_ID
    assert later.error["expectedCanonicalPayloadHash"] == first.receipt["canonicalPayloadHash"]
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 2,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_alternate_key_conflict_is_consumed(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    first = accept_publication(database_path, _request(idempotency_key="agent-original"))
    conflict = accept_publication(
        database_path,
        _request(
            idempotency_key="agent-alternate",
            publication=_publication(summary="different payload for same publication"),
        ),
    )

    assert isinstance(first, Accepted)
    assert isinstance(conflict, IdempotencyConflict)
    assert conflict.error["idempotencyKey"] == "agent-original"

    later = accept_publication(
        database_path,
        _request(
            idempotency_key="agent-alternate",
            publication=_publication(
                publication_id="22222222-2222-4222-8222-222222222222",
                source_sequence=2,
                summary="different publication reusing consumed conflict key",
            ),
        ),
    )

    assert isinstance(later, IdempotencyConflict)
    assert later.error["publicationId"] == PUBLICATION_ID
    assert (
        later.error["expectedCanonicalPayloadHash"]
        == conflict.error["receivedCanonicalPayloadHash"]
    )
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 2,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_non_canonical_semver_is_rejected(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"

    outcome = accept_publication(database_path, _request(contract_version="0.01.0"))

    assert isinstance(outcome, ValidationFailure)
    assert outcome.errors
    apply_migrations(database_path)
    assert _counts(database_path) == {
        "accepted_publications": 0,
        "publication_idempotency": 0,
        "acceptance_receipts": 0,
        "ingestion_jobs": 0,
    }


def test_unsupported_major_version_fails_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"

    outcome = accept_publication(database_path, _request(contract_version="1.0.0"))

    assert isinstance(outcome, UnsupportedVersion)
    assert outcome.error["supportedMajor"] == 0
    assert outcome.error["supportedMinor"] == 1
    apply_migrations(database_path)
    assert _counts(database_path) == {
        "accepted_publications": 0,
        "publication_idempotency": 0,
        "acceptance_receipts": 0,
        "ingestion_jobs": 0,
    }


def test_invalid_request_is_rejected(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    invalid = {"schemaVersion": "publication-acceptance-request-v1"}

    outcome = accept_publication(database_path, invalid)

    assert isinstance(outcome, ValidationFailure)
    assert outcome.errors
    apply_migrations(database_path)
    assert _counts(database_path) == {
        "accepted_publications": 0,
        "publication_idempotency": 0,
        "acceptance_receipts": 0,
        "ingestion_jobs": 0,
    }


def test_transaction_failure_leaves_no_half_acceptance(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    apply_migrations(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_publication BEFORE INSERT ON accepted_publications "
            "BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )

    with pytest.raises(sqlite3.IntegrityError):
        accept_publication(database_path, _request())

    assert _counts(database_path) == {
        "accepted_publications": 0,
        "publication_idempotency": 0,
        "acceptance_receipts": 0,
        "ingestion_jobs": 0,
    }


def test_restart_replay_remains_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    request = _request()

    first = accept_publication(database_path, request)
    second = accept_publication(database_path, request)

    assert isinstance(first, Accepted)
    assert isinstance(second, DuplicateReplay)
    assert second.receipt["receiptId"] == first.receipt["receiptId"]
    assert second.receipt["originalAcceptedAt"] == first.receipt["acceptedAt"]


def test_accepted_publication_survives_without_graphiti(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    outcome = accept_publication(database_path, _request())

    assert isinstance(outcome, Accepted)
    with sqlite3.connect(database_path) as connection:
        job = connection.execute("SELECT status, graphiti_status FROM ingestion_jobs").fetchone()
        publication = connection.execute(
            "SELECT publication_id FROM accepted_publications"
        ).fetchone()
    assert job == ("pending", "not_configured")
    assert publication == (PUBLICATION_ID,)
    health = build_health_report(database_path)
    assert health.status == "degraded"
    assert "publication.accept" in health.capabilities


def test_health_is_bootstrap_before_migration_and_degraded_after(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "router.sqlite3"

    assert build_health_report(database_path).status == "bootstrap"
    apply_migrations(database_path)
    assert build_health_report(database_path).status == "degraded"
