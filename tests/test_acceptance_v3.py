"""iris_memory#11: acceptance of the Graphiti-ready v3 publication envelope.

Covers the 2026-08-08 Notion boundary override: episode sources +
compartment revisions are the wire shape (no mandatory Segment /
standalone EvidenceSet / MemoryAssessmentDelta objects); acceptance
atomically persists the publication, the receipt/idempotency binding, the
ordered ingestion job and the immutable per-episode-source provenance;
duplicate replay binds to the exact canonical payload + contract version.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from iris_memory.acceptance import (
    Accepted,
    DuplicateReplay,
    IdempotencyConflict,
    SequenceConflict,
    UnsupportedVersion,
    ValidationFailure,
    _episode_source_canonical_hash,
    accept_publication,
)

PUBLICATION_ID = "11111111-2222-4333-8444-555555555555"
LINEAGE = "identity-0123456789abcdef"


def _episode_source(
    episode_id: str,
    frm: int,
    to: int,
    content: str,
    *,
    derived: bool = False,
    memrefs: list[str] | None = None,
) -> dict[str, Any]:
    src: dict[str, Any] = {
        "episodeId": episode_id,
        "lineageId": LINEAGE,
        "contextRange": {
            "contextLineageId": LINEAGE,
            "fromContextSeq": frm,
            "toContextSeq": to,
            "rangeHash": "b" * 64,
        },
        "sourceUnitIds": [f"u-{i}" for i in range(frm, to + 1)],
        "canonicalContent": content,
        "targetGroupId": f"group:{LINEAGE}",
        "temporal": {
            "startedAt": "2026-08-05T00:00:00.000Z",
            "endedAt": "2026-08-05T00:01:00.000Z",
        },
        "isDerivedOnly": derived,
        "derivation": {
            "memoryRefs": memrefs or [],
            "compartmentIds": ["comp-1"],
            "sourceContextUnitIds": [f"u-{i}" for i in range(1, frm)] if derived else [],
        },
    }
    src["episodeSourceHash"] = _episode_source_canonical_hash(src)
    return src


def _publication(
    *,
    publication_id: str = PUBLICATION_ID,
    source_sequence: int = 1,
    episode_sources: list[dict[str, Any]] | None = None,
    derived_only: bool | None = None,
) -> dict[str, Any]:
    episodes = episode_sources or [
        _episode_source("episode-1", 1, 2, "user: please remember this\nassistant: noted"),
        _episode_source("episode-2", 3, 3, "user: and this too"),
    ]
    pub: dict[str, Any] = {
        "schemaVersion": "historian-publication-v3",
        "publicationId": publication_id,
        "sourceSequence": source_sequence,
        "publishedAt": "2026-08-05T00:00:00.000Z",
        "payloadHash": "a" * 64,
        "contractVersion": "0.3.0",
        "projectionVersion": "graphiti-0.29.2",
        "lineageId": LINEAGE,
        "contextRange": {
            "contextLineageId": LINEAGE,
            "fromContextSeq": 1,
            "toContextSeq": max(e["contextRange"]["toContextSeq"] for e in episodes),
            "rangeHash": "b" * 64,
        },
        "compartmentRevisions": [
            {
                "compartmentId": "comp-1",
                "sequence": 1,
                "headContextSeq": 2,
                "summary": "fixture compartment",
                "memoryRefs": [],
            }
        ],
        "episodeSources": episodes,
        "derivationSummary": {
            "derivedOnly": (
                derived_only
                if derived_only is not None
                else any(e["isDerivedOnly"] for e in episodes)
            ),
            "memoryRefs": [],
        },
        "temporal": {
            "startedAt": "2026-08-05T00:00:00.000Z",
            "endedAt": "2026-08-05T00:01:00.000Z",
        },
    }
    # payloadHash covers the full canonical payload (sort_keys canonical JSON).
    import hashlib

    canonical = json.dumps(
        {k: v for k, v in pub.items() if k != "payloadHash"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    pub["payloadHash"] = hashlib.sha256(canonical).hexdigest()
    return pub


def _request(
    *,
    idempotency_key: str = "agent-run-v3-001",
    publication: dict[str, Any] | None = None,
    contract_version: str = "0.3.0",
) -> dict[str, Any]:
    return {
        "schemaVersion": "publication-acceptance-request-v3",
        "contractVersion": contract_version,
        "idempotencyKey": idempotency_key,
        "publication": publication if publication is not None else _publication(),
    }


def _episode_rows(database_path: Path) -> list[tuple[Any, ...]]:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            "SELECT publication_id, source_position, episode_id, lineage_id, "
            "target_group_id, is_derived_only, graphiti_status FROM "
            "accepted_episode_sources ORDER BY source_position"
        ).fetchall()


def test_v3_acceptance_persists_publication_receipt_job_and_episode_sources(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    outcome = accept_publication(database_path, _request())
    assert isinstance(outcome, Accepted)
    receipt = outcome.receipt
    assert receipt["schemaVersion"] == "acceptance-receipt-v3"
    assert receipt["contractVersion"] == "0.3.0"
    assert len(cast(list[object], receipt["episodeSourceHashes"])) == 2

    with sqlite3.connect(database_path) as connection:
        publication = connection.execute(
            "SELECT contract_version, canonical_payload_hash FROM accepted_publications "
            "WHERE publication_id = ?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert publication is not None and publication[0] == "0.3.0"
        job = connection.execute(
            "SELECT status, graphiti_status FROM ingestion_jobs WHERE publication_id = ?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert job is not None and job[0] == "pending"

    rows = _episode_rows(database_path)
    assert [r[1] for r in rows] == [1, 2]
    assert [r[2] for r in rows] == ["episode-1", "episode-2"]
    assert all(r[6] == "pending" for r in rows)


def test_v3_duplicate_replay_returns_v2_duplicate_receipt_bound_to_original(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    first = accept_publication(database_path, _request())
    assert isinstance(first, Accepted)

    replay = accept_publication(database_path, _request())
    assert isinstance(replay, DuplicateReplay)
    receipt = replay.receipt
    assert receipt["schemaVersion"] == "duplicate-replay-receipt-v2"
    assert receipt["status"] == "duplicate_replay"
    assert receipt["originalPublicationId"] == PUBLICATION_ID
    assert receipt["originalContractVersion"] == "0.3.0"
    assert receipt["originalCanonicalPayloadHash"] == first.receipt["canonicalPayloadHash"]

    # replay must NOT create more episode-source rows
    assert len(_episode_rows(database_path)) == 2


def test_v3_tampered_episode_source_fails_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    publication = _publication()
    sources = publication["episodeSources"]
    sources[0] = dict(sources[0], canonicalContent="tampered content")  # hash now stale
    outcome = accept_publication(database_path, _request(publication=publication))
    assert isinstance(outcome, ValidationFailure)
    assert any("canonical re-hash" in e for e in outcome.errors)
    # validation fails BEFORE any migration/acceptance — nothing persisted
    assert not database_path.exists()


def test_v3_swapped_idempotency_key_conflicts(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    assert isinstance(accept_publication(database_path, _request()), Accepted)
    other = _request(idempotency_key="other-key", publication=_publication(source_sequence=2))
    outcome = accept_publication(database_path, other)
    assert isinstance(outcome, IdempotencyConflict)
    assert (
        outcome.error["receivedCanonicalPayloadHash"]
        != outcome.error["expectedCanonicalPayloadHash"]
    )


def test_v3_stale_sequence_conflicts(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    assert isinstance(accept_publication(database_path, _request()), Accepted)
    stale = _request(idempotency_key="stale-key", publication=_publication(source_sequence=1))
    # same sequence, different publication id
    stale["publication"]["publicationId"] = "22222222-3333-4444-8555-666666666666"
    outcome = accept_publication(database_path, stale)
    assert isinstance(outcome, SequenceConflict)


def test_v3_derived_only_publication_accepted_with_derivation_provenance(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    episodes = [
        _episode_source("episode-d", 1, 2, "recalled summary", derived=True, memrefs=["mem-1"])
    ]
    publication = _publication(episode_sources=episodes, derived_only=True)
    outcome = accept_publication(database_path, _request(publication=publication))
    assert isinstance(outcome, Accepted)
    rows = _episode_rows(database_path)
    assert len(rows) == 1 and rows[0][5] == 1  # is_derived_only flag persisted


def test_v3_derived_only_publication_rejects_new_observation_sources(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    episodes = [
        _episode_source("episode-1", 1, 2, "user: new observation truth"),
    ]
    publication = _publication(episode_sources=episodes, derived_only=True)
    outcome = accept_publication(database_path, _request(publication=publication))
    assert isinstance(outcome, ValidationFailure)
    assert any("non-derived episode sources" in e for e in outcome.errors)


def test_v3_source_range_outside_publication_range_rejected(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    publication = _publication()
    # push the second source past the publication toContextSeq
    sources = publication["episodeSources"]
    sources[1]["contextRange"]["toContextSeq"] = 99
    sources[1]["episodeSourceHash"] = _episode_source_canonical_hash(sources[1])
    outcome = accept_publication(database_path, _request(publication=publication))
    assert isinstance(outcome, ValidationFailure)
    assert any("must lie inside" in e for e in outcome.errors)


def test_v3_unknown_minor_still_unsupported(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    outcome = accept_publication(
        database_path, _request(contract_version="0.4.0", publication=_publication())
    )
    assert isinstance(outcome, UnsupportedVersion)


def test_v3_non_object_body_fails_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    outcome = accept_publication(database_path, ["not", "an", "object"])
    assert isinstance(outcome, ValidationFailure)


def test_v3_identical_episode_content_across_publications_is_legal(
    tmp_path: Path,
) -> None:
    """iris_memory#11 (PK fix): the same deterministic episode source content
    can legitimately appear in DIFFERENT publications (replayed/rebuilt
    batches) — each publication keeps its own immutable row."""
    database_path = tmp_path / "data" / "router.sqlite3"
    first = _publication(publication_id=PUBLICATION_ID, source_sequence=1)
    outcome = accept_publication(database_path, _request(publication=first))
    assert isinstance(outcome, Accepted)

    second = _publication(
        publication_id="22222222-3333-4444-8555-666666666666",
        source_sequence=2,
    )
    outcome2 = accept_publication(
        database_path, _request(idempotency_key="agent-run-v3-002", publication=second)
    )
    assert isinstance(outcome2, Accepted)
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT publication_id, episode_source_hash FROM accepted_episode_sources "
            "ORDER BY publication_id"
        ).fetchall()
    assert len(rows) == 4  # 2 publications × 2 sources, no UNIQUE collision
    assert rows[0][0] != rows[2][0]


def test_v3_schema_version_mismatch_fails_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    request = _request()
    request["schemaVersion"] = "publication-acceptance-request-v2"
    outcome = accept_publication(database_path, request)
    assert isinstance(outcome, ValidationFailure)
