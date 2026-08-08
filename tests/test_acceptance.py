import json
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
        "publication_idempotency": 1,
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
        "publication_idempotency": 1,
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


def test_alternate_key_conflict_replay_returns_same_conflict(tmp_path: Path) -> None:
    """A conflicted alternate key is not persisted before classification, so a
    replay of the exact same conflict request returns the same typed conflict
    instead of crashing on a missing receipt or misreporting duplicate_replay."""
    database_path = tmp_path / "router.sqlite3"
    first = accept_publication(database_path, _request(idempotency_key="agent-original"))
    request = _request(
        idempotency_key="agent-alternate",
        publication=_publication(summary="different payload for same publication"),
    )
    conflict = accept_publication(database_path, request)
    replay = accept_publication(database_path, request)

    assert isinstance(first, Accepted)
    assert isinstance(conflict, IdempotencyConflict)
    assert conflict.error["idempotencyKey"] == "agent-original"
    assert isinstance(replay, IdempotencyConflict)
    assert replay.error["idempotencyKey"] == conflict.error["idempotencyKey"]
    assert replay.error["publicationId"] == conflict.error["publicationId"]
    assert (
        replay.error["expectedCanonicalPayloadHash"]
        == conflict.error["expectedCanonicalPayloadHash"]
    )
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 1,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_sequence_conflict_replay_returns_same_conflict(tmp_path: Path) -> None:
    """A sequence-conflicted key is not persisted before classification, so a
    replay of the exact same request returns source_sequence_conflict instead of
    crashing on a missing receipt."""
    database_path = tmp_path / "router.sqlite3"
    accept_publication(database_path, _request(idempotency_key="agent-original"))
    request = _request(
        idempotency_key="agent-alternate",
        publication=_publication(
            publication_id="22222222-2222-4222-8222-222222222222",
            source_sequence=1,
            summary="same sequence, different publication",
        ),
    )
    first = accept_publication(database_path, request)
    replay = accept_publication(database_path, request)

    assert isinstance(first, SequenceConflict)
    assert isinstance(replay, SequenceConflict)
    assert replay.error["sourceSequence"] == first.error["sourceSequence"]
    assert replay.error["publicationId"] == first.error["publicationId"]
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 1,
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
    assert outcome.error["supportedMinor"] == 3
    assert outcome.error["supportedMinors"] == [1, 2, 3]
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


# --- iris_memory#6: v2 provenance validation matrix ---------------------------


def _publication_v2(
    *,
    publication_id: str = PUBLICATION_ID,
    source_sequence: int = 1,
    derived_only: bool = False,
    basis: list[dict[str, Any]] | None = None,
    evidence_count: int | None = None,
    from_seq: int = 1,
    to_seq: int = 4,
) -> dict[str, Any]:
    effective_basis = (
        basis
        if basis is not None
        else [
            {
                "contextUnitId": "input-e-1",
                "contextSeq": 1,
                "runtimeEventId": "evt-1",
                "contentHash": HASH_64,
                "historianDisposition": "include",
            }
        ]
    )
    return {
        "schemaVersion": "historian-publication-v2",
        "publicationId": publication_id,
        "sourceSequence": source_sequence,
        "publishedAt": "2026-08-05T00:00:00Z",
        "payloadHash": HASH_64,
        "contextRange": {
            "contextLineageId": "identity-0123456789abcdef",
            "fromContextSeq": from_seq,
            "toContextSeq": to_seq,
            "rangeHash": HASH_64,
        },
        "semanticSourceVersion": "context-unit-v1",
        "compartmentCount": 1,
        "segmentCount": 1,
        "evidenceCount": evidence_count if evidence_count is not None else len(effective_basis),
        "evidenceBasis": effective_basis,
        "derivedOnly": derived_only,
        "summary": "v2 publication",
    }


def _request_v2(
    *,
    idempotency_key: str = "v2-run-001",
    publication: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": "publication-acceptance-request-v2",
        "contractVersion": "0.2.0",
        "idempotencyKey": idempotency_key,
        "publication": publication if publication is not None else _publication_v2(),
    }


def test_v2_publication_accepts_with_full_provenance(tmp_path: Path) -> None:
    outcome = accept_publication(tmp_path / "router.sqlite3", _request_v2())
    assert isinstance(outcome, Accepted)


def test_v2_evidence_count_mismatch_fails_closed(tmp_path: Path) -> None:
    publication = _publication_v2(
        basis=[
            {
                "contextUnitId": "a",
                "contextSeq": 1,
                "runtimeEventId": "evt-1",
                "contentHash": HASH_64,
                "historianDisposition": "include",
            },
            {
                "contextUnitId": "b",
                "contextSeq": 2,
                "runtimeEventId": "evt-2",
                "contentHash": HASH_64,
                "historianDisposition": "reference_only",
            },
        ],
        evidence_count=2,  # reference_only must NOT count
    )
    outcome = accept_publication(tmp_path / "router.sqlite3", _request_v2(publication=publication))
    assert isinstance(outcome, ValidationFailure)
    assert any("evidenceCount" in e and "include basis count" in e for e in outcome.errors)


def test_v2_derived_only_cannot_claim_new_evidence(tmp_path: Path) -> None:
    publication = _publication_v2(derived_only=True, evidence_count=1)
    outcome = accept_publication(tmp_path / "router.sqlite3", _request_v2(publication=publication))
    assert isinstance(outcome, ValidationFailure)
    assert any("derivedOnly" in e for e in outcome.errors)


def test_v2_derived_only_with_only_derivation_refs_accepts(tmp_path: Path) -> None:
    publication = _publication_v2(
        derived_only=True,
        evidence_count=0,
        basis=[
            {
                "contextUnitId": "recall-e-3",
                "contextSeq": 3,
                "runtimeEventId": "evt-3",
                "contentHash": HASH_64,
                "historianDisposition": "exclude",
                "derivationRefs": {
                    "memoryRefs": ["mem-1"],
                    "compartmentIds": [],
                    "sourceContextUnitIds": ["input-e-1"],
                },
            }
        ],
    )
    outcome = accept_publication(tmp_path / "router.sqlite3", _request_v2(publication=publication))
    assert isinstance(outcome, Accepted)


def test_v2_inverted_context_range_fails_closed(tmp_path: Path) -> None:
    publication = _publication_v2(from_seq=4, to_seq=1)
    outcome = accept_publication(tmp_path / "router.sqlite3", _request_v2(publication=publication))
    assert isinstance(outcome, ValidationFailure)
    assert any("contextRange" in e for e in outcome.errors)


def test_v2_include_basis_without_runtime_event_id_fails_closed(tmp_path: Path) -> None:
    publication = _publication_v2(
        basis=[
            {
                "contextUnitId": "a",
                "contextSeq": 1,
                "contentHash": HASH_64,
                "historianDisposition": "include",
            }
        ]
    )
    outcome = accept_publication(tmp_path / "router.sqlite3", _request_v2(publication=publication))
    assert isinstance(outcome, ValidationFailure)
    assert any("runtimeEventId" in e for e in outcome.errors)


def test_v2_replay_returns_duplicate_receipt(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    first = accept_publication(database_path, _request_v2(idempotency_key="replay-key"))
    assert isinstance(first, Accepted)
    second = accept_publication(database_path, _request_v2(idempotency_key="replay-key"))
    assert isinstance(second, DuplicateReplay)


def test_v2_reused_key_with_changed_provenance_conflicts(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    first = accept_publication(database_path, _request_v2(idempotency_key="mut-key"))
    assert isinstance(first, Accepted)
    changed = _publication_v2(to_seq=5)  # different context range => different hash
    second = accept_publication(
        database_path, _request_v2(idempotency_key="mut-key", publication=changed)
    )
    assert isinstance(second, IdempotencyConflict)


# --- iris_memory#6: legacy rows keep their original contract version ----------


def test_v2_acceptance_stores_version_and_v1_rows_stay_unfabricated(tmp_path: Path) -> None:
    """Legacy 0.1.x rows keep their original contract version; nothing invents
    contextUnitId/rangeHash/derivationRefs for them; v2 rows are tagged 0.2.0."""
    database_path = tmp_path / "router.sqlite3"
    v1_outcome = accept_publication(database_path, _request(contract_version="0.1.0"))
    assert isinstance(v1_outcome, Accepted)
    v2_outcome = accept_publication(
        database_path,
        _request_v2(
            idempotency_key="v2-legacy-key",
            publication=_publication_v2(
                publication_id="22222222-3333-4444-8555-666666666666",
                source_sequence=2,
            ),
        ),
    )
    assert isinstance(v2_outcome, Accepted)

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT publication_id, contract_version FROM accepted_publications "
            "ORDER BY source_sequence"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][1] == "0.1.0", "v1 row must keep its original contract version"
    assert rows[1][1] == "0.2.0", "v2 row must be tagged 0.2.0"
    # The v1 payload JSON must not have gained provenance fields (no fabrication).
    with sqlite3.connect(database_path) as connection:
        payload = connection.execute(
            "SELECT payload_json FROM accepted_publications WHERE publication_id = ?",
            (rows[0][0],),
        ).fetchone()[0]
    parsed = json.loads(payload)
    assert "contextRange" not in parsed, "legacy v1 row must not be retrofitted with provenance"
    assert "evidenceBasis" not in parsed, "legacy v1 row must not be retrofitted with basis refs"


# --- iris_memory#6 review: non-object bodies must fail cleanly ---------------


def test_non_dict_request_body_fails_closed(tmp_path: Path) -> None:
    """A non-object body (array/scalar/null) must be a clean validation
    failure, never an unhandled AttributeError (review BLOCKING)."""
    database_path = tmp_path / "router.sqlite3"
    for bad in ([], "x", None, 42):
        outcome = accept_publication(database_path, bad)
        assert isinstance(outcome, ValidationFailure), f"{bad!r} must be ValidationFailure"
        assert any("JSON object" in e for e in outcome.errors)
