"""Durable Publication acceptance without Graphiti/Neo4j."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from iris_memory.contracts.validation import validate_instance
from iris_memory.db import apply_migrations

SUPPORTED_MAJOR = 0
SUPPORTED_MINORS = (1, 2, 3)
_REQUEST_SCHEMA = "publication-acceptance-request-v1.schema.json"
_PUBLICATION_SCHEMA = "historian-publication-v1.schema.json"
_REQUEST_SCHEMA_V2 = "publication-acceptance-request-v2.schema.json"
_PUBLICATION_SCHEMA_V2 = "historian-publication-v2.schema.json"
_REQUEST_SCHEMA_V3 = "publication-acceptance-request-v3.schema.json"
_PUBLICATION_SCHEMA_V3 = "historian-publication-v3.schema.json"
_SEMVER_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class Accepted:
    """A publication was durably accepted."""

    status: Literal["accepted"]
    receipt: dict[str, object]


@dataclass(frozen=True, slots=True)
class DuplicateReplay:
    """The exact publication was already accepted."""

    status: Literal["duplicate_replay"]
    receipt: dict[str, object]


@dataclass(frozen=True, slots=True)
class IdempotencyConflict:
    """The idempotency identity was reused with different content."""

    status: Literal["idempotency_conflict"]
    error: dict[str, object]


@dataclass(frozen=True, slots=True)
class UnsupportedVersion:
    """The requested contract major/minor version is not supported."""

    status: Literal["unsupported_contract_version"]
    error: dict[str, object]


@dataclass(frozen=True, slots=True)
class SequenceConflict:
    """A different publication already owns the requested source sequence."""

    status: Literal["source_sequence_conflict"]
    error: dict[str, object]


@dataclass(frozen=True, slots=True)
class ValidationFailure:
    """The request failed schema validation."""

    status: Literal["validation_failed"]
    errors: tuple[str, ...]


type AcceptanceOutcome = (
    Accepted
    | DuplicateReplay
    | IdempotencyConflict
    | UnsupportedVersion
    | SequenceConflict
    | ValidationFailure
)


def _parse_version(version: str) -> tuple[int, int, int] | None:
    if _SEMVER_PATTERN.fullmatch(version) is None:
        return None
    parts = version.split(".")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _receipt_id(publication_id: str, canonical_hash: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"iris-memory:v1:{publication_id}:{canonical_hash}"))


def _build_receipt(
    publication_id: str,
    canonical_hash: str,
    contract_version: str,
    accepted_at: str,
) -> dict[str, object]:
    return {
        "schemaVersion": "acceptance-receipt-v1",
        "status": "accepted",
        "receiptId": _receipt_id(publication_id, canonical_hash),
        "publicationId": publication_id,
        "canonicalPayloadHash": canonical_hash,
        "contractVersion": contract_version,
        "acceptedAt": accepted_at,
    }


def _build_duplicate_receipt(original_receipt: dict[str, object]) -> dict[str, object]:
    if original_receipt.get("schemaVersion") == "acceptance-receipt-v3":
        # iris_memory#11: v3 duplicates are bound to the exact canonical
        # payload AND contract version (v2 duplicate shape).
        return {
            "schemaVersion": "duplicate-replay-receipt-v2",
            "status": "duplicate_replay",
            "originalPublicationId": original_receipt["publicationId"],
            "originalContractVersion": original_receipt["contractVersion"],
            "originalCanonicalPayloadHash": original_receipt["canonicalPayloadHash"],
            "originalAcceptedAt": original_receipt["acceptedAt"],
            "replayedAt": datetime.now(UTC).isoformat(),
        }
    return {
        "schemaVersion": "duplicate-replay-receipt-v1",
        "status": "duplicate_replay",
        "receiptId": original_receipt["receiptId"],
        "publicationId": original_receipt["publicationId"],
        "canonicalPayloadHash": original_receipt["canonicalPayloadHash"],
        "contractVersion": original_receipt["contractVersion"],
        "originalAcceptedAt": original_receipt["acceptedAt"],
    }


def _build_conflict_error(
    idempotency_key: str,
    publication_id: str,
    expected_hash: str,
    received_hash: str,
) -> dict[str, object]:
    return {
        "schemaVersion": "idempotency-conflict-error-v1",
        "error": "idempotency_conflict",
        "idempotencyKey": idempotency_key,
        "publicationId": publication_id,
        "expectedCanonicalPayloadHash": expected_hash,
        "receivedCanonicalPayloadHash": received_hash,
    }


def _build_unsupported_error(contract_version: str) -> dict[str, object]:
    return {
        "schemaVersion": "unsupported-version-error-v1",
        "error": "unsupported_contract_version",
        "contractVersion": contract_version,
        "supportedMajor": SUPPORTED_MAJOR,
        "supportedMinor": max(SUPPORTED_MINORS),
        "supportedMinors": list(SUPPORTED_MINORS),
        "message": (
            f"contract {contract_version} is outside the supported "
            f"0.{'/'.join(str(m) for m in SUPPORTED_MINORS)}.x wire contract"
        ),
    }


def _build_sequence_conflict_error(
    publication_id: str,
    source_sequence: int,
    expected_hash: str,
    received_hash: str,
) -> dict[str, object]:
    return {
        "schemaVersion": "sequence-conflict-error-v1",
        "error": "source_sequence_conflict",
        "publicationId": publication_id,
        "sourceSequence": source_sequence,
        "expectedCanonicalPayloadHash": expected_hash,
        "receivedCanonicalPayloadHash": received_hash,
    }


def _load_stored_receipt(
    connection: sqlite3.Connection,
    publication_id: str,
) -> dict[str, object]:
    row = connection.execute(
        "SELECT receipt_json FROM acceptance_receipts WHERE publication_id = ?",
        (publication_id,),
    ).fetchone()
    assert row is not None
    return cast(dict[str, object], json.loads(row[0]))


def _validate_v2_provenance(publication: object) -> tuple[str, ...]:
    """Structural provenance invariants for historian-publication-v2 (iris_memory#6).

    - derivedOnly publications must not claim new supporting Evidence
      (anti-echo: assistant restatements of memory/compartment/work/source
      refs cannot independently produce new Evidence);
    - evidenceCount must equal the number of include-disposition basis refs;
    - contextRange must be monotonic (from <= to) and non-empty;
    - include basis refs must carry runtimeEventId (stable attribution).
    """
    publication_dict = cast(dict[str, object], publication)
    errors: list[str] = []

    context_range = cast(dict[str, object], publication_dict["contextRange"])
    from_seq = cast(int, context_range["fromContextSeq"])
    to_seq = cast(int, context_range["toContextSeq"])
    if from_seq > to_seq:
        errors.append("contextRange.fromContextSeq must be <= toContextSeq")
    if to_seq < 1:
        errors.append("contextRange.toContextSeq must be >= 1")

    basis = cast(list[object], publication_dict["evidenceBasis"])
    include_count = 0
    for item in basis:
        ref = cast(dict[str, object], item)
        disposition = str(ref.get("historianDisposition", ""))
        if disposition == "include":
            include_count += 1
            if not str(ref.get("runtimeEventId", "")).strip():
                errors.append("include basis ref must carry runtimeEventId")

        if disposition == "exclude" and ref.get("derivationRefs") is None:
            # exclude must not enter the analysis basis; it may appear only as
            # derivation/suppression context, never as a supporting ref.
            errors.append("exclude basis ref must carry derivationRefs (suppression context)")

    declared_count = cast(int, publication_dict["evidenceCount"])
    if declared_count != include_count:
        errors.append(
            f"evidenceCount {declared_count} != include basis count {include_count} "
            "(reference_only/exclude never increase evidence count)"
        )

    derived_only = bool(publication_dict["derivedOnly"])
    if derived_only and include_count > 0:
        errors.append(
            "derivedOnly publication must not carry include-disposition basis refs "
            "(derived-only content cannot produce new Evidence)"
        )

    return tuple(errors)


def _episode_source_canonical_hash(source: dict[str, object]) -> str:
    """iris_memory#11: deterministic canonical hash of ONE GraphitiEpisodeSource.

    Covers identity + provenance + content (episodeId, lineageId, context
    range, ordered source unit ids, canonical content, target group,
    temporal, derivation/anti-echo flags). A changed provenance/content is a
    DIFFERENT payload and can never duplicate-replay as equivalent.
    """
    canonical = {
        "episodeId": source["episodeId"],
        "lineageId": source["lineageId"],
        "contextRange": source["contextRange"],
        "sourceUnitIds": source["sourceUnitIds"],
        "canonicalContent": source["canonicalContent"],
        "targetGroupId": source["targetGroupId"],
        "temporal": source["temporal"],
        "isDerivedOnly": source["isDerivedOnly"],
        "derivation": source["derivation"],
    }
    return _sha256(canonical)


def _validate_v3_provenance(publication: object) -> tuple[str, ...]:
    """Structural provenance invariants for historian-publication-v3
    (iris_memory#11, 2026-08-08 Graphiti-ready boundary override):

    - publication contextRange monotonic and non-empty;
    - every episode source's contextRange lies INSIDE the publication range;
    - every episode source's declared episodeSourceHash equals the
      deterministic canonical re-hash (tampered/swapped provenance fails);
    - episode source hashes are unique within the batch;
    - a derived-only publication must not contain new observation sources
      (anti-echo: every source isDerivedOnly);
    - derived-only sources must carry derivation provenance
      (memoryRefs or sourceContextUnitIds — recalled/derived material is
      never treated as new observation truth).
    """
    publication_dict = cast(dict[str, object], publication)
    errors: list[str] = []

    context_range = cast(dict[str, object], publication_dict["contextRange"])
    from_seq = cast(int, context_range["fromContextSeq"])
    to_seq = cast(int, context_range["toContextSeq"])
    if from_seq > to_seq:
        errors.append("contextRange.fromContextSeq must be <= toContextSeq")
    if to_seq < 1:
        errors.append("contextRange.toContextSeq must be >= 1")

    episode_sources = cast(list[object], publication_dict["episodeSources"])
    if not episode_sources:
        errors.append("episodeSources must contain at least one episode source")

    seen_hashes: set[str] = set()
    for item in episode_sources:
        source = cast(dict[str, object], item)
        source_range = cast(dict[str, object], source["contextRange"])
        s_from = cast(int, source_range["fromContextSeq"])
        s_to = cast(int, source_range["toContextSeq"])
        if s_from < from_seq or s_to > to_seq:
            errors.append(
                f"episode source {source.get('episodeId')!r} contextRange "
                f"[{s_from}..{s_to}] must lie inside the publication range [{from_seq}..{to_seq}]"
            )
        declared_hash = str(source["episodeSourceHash"])
        recomputed = _episode_source_canonical_hash(source)
        if declared_hash != recomputed:
            errors.append(
                f"episode source {source.get('episodeId')!r} declared hash "
                f"{declared_hash[:12]} != canonical re-hash {recomputed[:12]} "
                "(tampered/swapped provenance fails closed)"
            )
        if declared_hash in seen_hashes:
            errors.append(f"duplicate episodeSourceHash {declared_hash[:12]} in batch")
        seen_hashes.add(declared_hash)

        derived = bool(source["isDerivedOnly"])
        derivation = cast(dict[str, object], source["derivation"])
        if derived:
            memory_refs = cast(list[object], derivation.get("memoryRefs", []))
            source_context_unit_ids = cast(list[object], derivation.get("sourceContextUnitIds", []))
            if not memory_refs and not source_context_unit_ids:
                errors.append(
                    f"derived-only episode source {source.get('episodeId')!r} must carry "
                    "derivation provenance (memoryRefs or sourceContextUnitIds)"
                )

    derivation_summary = cast(dict[str, object], publication_dict["derivationSummary"])
    pub_derived_only = bool(derivation_summary["derivedOnly"])
    if pub_derived_only:
        for item in episode_sources:
            source = cast(dict[str, object], item)
            if not bool(source["isDerivedOnly"]):
                errors.append(
                    "derivedOnly publication must not contain non-derived episode sources "
                    "(derived-only content cannot produce new observation truth)"
                )

    return tuple(errors)


def _build_receipt_v3(
    publication_id: str,
    canonical_hash: str,
    contract_version: str,
    accepted_at: str,
    episode_source_hashes: list[str],
) -> dict[str, object]:
    return {
        "schemaVersion": "acceptance-receipt-v3",
        "status": "accepted",
        "receiptId": _receipt_id(publication_id, canonical_hash),
        "publicationId": publication_id,
        "contractVersion": contract_version,
        "canonicalPayloadHash": canonical_hash,
        "episodeSourceHashes": episode_source_hashes,
        "acceptedAt": accepted_at,
    }


def _persist_episode_sources(
    connection: sqlite3.Connection,
    publication_id: str,
    publication: dict[str, object],
    accepted_at: str,
) -> list[str]:
    """Insert the immutable episode-source rows for an accepted v3
    publication (source_position order) and return the ordered hashes."""
    hashes: list[str] = []
    episode_sources = cast(list[object], publication["episodeSources"])
    for position, item in enumerate(episode_sources, start=1):
        source = cast(dict[str, object], item)
        source_hash = str(source["episodeSourceHash"])
        hashes.append(source_hash)
        source_range = cast(dict[str, object], source["contextRange"])
        from_seq = cast(int, source_range["fromContextSeq"])
        to_seq = cast(int, source_range["toContextSeq"])
        connection.execute(
            "INSERT INTO accepted_episode_sources"
            "(episode_source_hash, publication_id, source_position, episode_id, "
            "lineage_id, from_context_seq, to_context_seq, target_group_id, "
            "canonical_content_hash, is_derived_only, source_json, graphiti_status, "
            "attempt_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "'pending', 0, ?)",
            (
                source_hash,
                publication_id,
                position,
                str(source["episodeId"]),
                str(source["lineageId"]),
                from_seq,
                to_seq,
                str(source["targetGroupId"]),
                _sha256(str(source["canonicalContent"])),
                1 if bool(source["isDerivedOnly"]) else 0,
                _canonical_json_bytes(source).decode("utf-8"),
                accepted_at,
            ),
        )
    return hashes


def accept_publication(database_path: Path, request: object) -> AcceptanceOutcome:
    """Validate, deduplicate and atomically accept one HistorianPublication.

    Schema dispatch is driven by contractVersion: 0.1.x validates against the
    immutable v1 schemas; 0.2.x (iris_memory#6) validates against the v2
    schemas and the structural provenance invariants; 0.3.x (iris_memory#11)
    validates the Graphiti-ready v3 envelope and persists the immutable
    per-episode-source rows. A request whose schemaVersion disagrees with
    its contractVersion fails closed.
    """
    if not isinstance(request, dict):
        # Shape guard BEFORE version parsing: a non-object body (array,
        # scalar, null) must fail as a clean validation_failed, never as an
        # unhandled AttributeError that breaks the HTTP connection.
        return ValidationFailure(
            status="validation_failed",
            errors=("request body must be a JSON object",),
        )
    request_dict_peek = cast(dict[str, object], request)
    contract_version = str(request_dict_peek.get("contractVersion", ""))
    parsed = _parse_version(contract_version)
    if parsed is None:
        # Malformed/missing version is a validation failure, not a version
        # negotiation issue (v1 semantics preserved).
        return ValidationFailure(
            status="validation_failed",
            errors=(f"contractVersion must be a canonical semver, got {contract_version!r}",),
        )
    if parsed[0] != SUPPORTED_MAJOR or parsed[1] not in SUPPORTED_MINORS:
        return UnsupportedVersion(
            status="unsupported_contract_version",
            error=_build_unsupported_error(contract_version),
        )

    if parsed[1] == 2:
        request_valid, request_errors = validate_instance(_REQUEST_SCHEMA_V2, request)
        if not request_valid:
            return ValidationFailure(status="validation_failed", errors=request_errors)
        publication = cast(dict[str, object], request_dict_peek["publication"])
        publication_valid, publication_errors = validate_instance(
            _PUBLICATION_SCHEMA_V2, publication
        )
        if not publication_valid:
            return ValidationFailure(status="validation_failed", errors=publication_errors)
        provenance_errors = _validate_v2_provenance(publication)
        if provenance_errors:
            return ValidationFailure(status="validation_failed", errors=provenance_errors)
    elif parsed[1] == 3:
        # iris_memory#11: the Graphiti-ready boundary (2026-08-08 Notion
        # override) — episode sources + compartment revisions, no mandatory
        # Segment/EvidenceSet/MemoryAssessmentDelta wire objects.
        request_valid, request_errors = validate_instance(_REQUEST_SCHEMA_V3, request)
        if not request_valid:
            return ValidationFailure(status="validation_failed", errors=request_errors)
        publication = cast(dict[str, object], request_dict_peek["publication"])
        publication_valid, publication_errors = validate_instance(
            _PUBLICATION_SCHEMA_V3, publication
        )
        if not publication_valid:
            return ValidationFailure(status="validation_failed", errors=publication_errors)
        provenance_errors = _validate_v3_provenance(publication)
        if provenance_errors:
            return ValidationFailure(status="validation_failed", errors=provenance_errors)
    else:
        request_valid, request_errors = validate_instance(_REQUEST_SCHEMA, request)
        if not request_valid:
            return ValidationFailure(status="validation_failed", errors=request_errors)
        publication = cast(dict[str, object], request_dict_peek["publication"])
        publication_valid, publication_errors = validate_instance(_PUBLICATION_SCHEMA, publication)
        if not publication_valid:
            return ValidationFailure(status="validation_failed", errors=publication_errors)

    publication_dict = publication
    publication_id = str(publication_dict["publicationId"])
    source_sequence = cast(int, publication_dict["sourceSequence"])
    canonical_hash = _sha256(publication_dict)
    idempotency_key = str(request_dict_peek["idempotencyKey"])

    apply_migrations(database_path)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            accepted_at = datetime.now(UTC).isoformat()
            idempotency_row = connection.execute(
                "SELECT publication_id, canonical_payload_hash, accepted_at "
                "FROM publication_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if idempotency_row is not None:
                existing_id = str(idempotency_row[0])
                existing_hash = str(idempotency_row[1])
                if existing_hash == canonical_hash:
                    original = _load_stored_receipt(connection, existing_id)
                    return DuplicateReplay(
                        status="duplicate_replay",
                        receipt=_build_duplicate_receipt(original),
                    )
                return IdempotencyConflict(
                    status="idempotency_conflict",
                    error=_build_conflict_error(
                        idempotency_key,
                        existing_id,
                        existing_hash,
                        canonical_hash,
                    ),
                )

            publication_row = connection.execute(
                "SELECT canonical_payload_hash, receipt_id, accepted_at "
                "FROM accepted_publications WHERE publication_id = ?",
                (publication_id,),
            ).fetchone()
            if publication_row is not None:
                existing_hash = str(publication_row[0])
                if existing_hash == canonical_hash:
                    connection.execute(
                        "INSERT INTO publication_idempotency"
                        "(idempotency_key, publication_id, canonical_payload_hash, accepted_at) "
                        "VALUES (?, ?, ?, ?)",
                        (idempotency_key, publication_id, canonical_hash, accepted_at),
                    )
                    original = _load_stored_receipt(connection, publication_id)
                    return DuplicateReplay(
                        status="duplicate_replay",
                        receipt=_build_duplicate_receipt(original),
                    )
                stored_key = connection.execute(
                    "SELECT idempotency_key FROM publication_idempotency "
                    "WHERE publication_id = ? ORDER BY accepted_at ASC, rowid ASC LIMIT 1",
                    (publication_id,),
                ).fetchone()
                original_key = str(stored_key[0]) if stored_key is not None else idempotency_key
                return IdempotencyConflict(
                    status="idempotency_conflict",
                    error=_build_conflict_error(
                        original_key,
                        publication_id,
                        existing_hash,
                        canonical_hash,
                    ),
                )

            sequence_row = connection.execute(
                "SELECT publication_id, canonical_payload_hash "
                "FROM accepted_publications WHERE source_sequence = ?",
                (source_sequence,),
            ).fetchone()
            if sequence_row is not None and str(sequence_row[0]) != publication_id:
                return SequenceConflict(
                    status="source_sequence_conflict",
                    error=_build_sequence_conflict_error(
                        publication_id,
                        source_sequence,
                        str(sequence_row[1]),
                        canonical_hash,
                    ),
                )

            receipt = _build_receipt(publication_id, canonical_hash, contract_version, accepted_at)
            receipt_json = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
            payload_json = json.dumps(publication_dict, ensure_ascii=False, sort_keys=True)
            job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"iris-memory:job:v1:{publication_id}"))

            connection.execute(
                "INSERT INTO publication_idempotency"
                "(idempotency_key, publication_id, canonical_payload_hash, accepted_at) "
                "VALUES (?, ?, ?, ?)",
                (idempotency_key, publication_id, canonical_hash, accepted_at),
            )
            connection.execute(
                "INSERT INTO accepted_publications"
                "(publication_id, contract_version, source_sequence, canonical_payload_hash, "
                "receipt_id, accepted_at, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    publication_id,
                    contract_version,
                    source_sequence,
                    canonical_hash,
                    str(receipt["receiptId"]),
                    accepted_at,
                    payload_json,
                ),
            )
            # iris_memory#11: v3 episode sources are persisted AFTER the
            # accepted_publications row (FK), inside the SAME transaction —
            # acceptance is atomic across publication + receipt + job +
            # immutable episode-source provenance.
            if parsed[1] == 3:
                episode_source_hashes = _persist_episode_sources(
                    connection, publication_id, publication_dict, accepted_at
                )
                receipt = _build_receipt_v3(
                    publication_id,
                    canonical_hash,
                    contract_version,
                    accepted_at,
                    episode_source_hashes,
                )
                receipt_json = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
            connection.execute(
                "INSERT INTO acceptance_receipts"
                "(receipt_id, publication_id, status, receipt_json, accepted_at) "
                "VALUES (?, ?, 'accepted', ?, ?)",
                (str(receipt["receiptId"]), publication_id, receipt_json, accepted_at),
            )
            connection.execute(
                "INSERT INTO ingestion_jobs"
                "(job_id, publication_id, source_sequence, status, graphiti_status, "
                "attempt_count, created_at, updated_at) VALUES (?, ?, ?, 'pending', "
                "'not_configured', 0, ?, ?)",
                (job_id, publication_id, source_sequence, accepted_at, accepted_at),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise

    return Accepted(status="accepted", receipt=receipt)
