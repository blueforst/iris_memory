"""iris_memory#11: deterministic Graphiti episode ingestion mapping.

The accepted GraphitiEpisodeSource rows (accepted_episode_sources) map
deterministically to the narrow Graphiti adapter input:

- `map_episode_source_to_graphiti_input` — memory-owned identity, canonical
  content, target group, temporal metadata and Context provenance. NO
  Graphiti SDK objects, Neo4j records, embeddings or internal graph UUIDs
  cross this boundary (the adapter is the only place the locked
  graphiti-core SDK is touched).
- `ingest_publication_episodes` — ordered ingestion of one publication's
  episode sources in source_position order. Idempotent per
  episode_source_hash: an already-ingested source is never re-mapped or
  re-added; a crash mid-batch resumes from the next pending source; every
  attempt is recorded (attempt_count / graphiti_status / graphiti_episode_key
  / last_error) so the Publication→episode mapping is never lost.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast


@dataclass(frozen=True, slots=True)
class GraphitiEpisodeInput:
    """The narrow, memory-owned input to Graphiti.add_episode."""

    episode_key: str
    name: str
    text: str
    group: str
    timestamp: str
    source_context: dict[str, object]


class GraphitiAdapter(Protocol):
    """Narrow adapter boundary: the ONLY place Graphiti/Neo4j is touched.

    Implementations receive the memory-owned GraphitiEpisodeInput and return
    the memory-owned episode key; they never leak SDK objects or internal
    graph ids into the contract.
    """

    def add_episode(self, episode_input: GraphitiEpisodeInput) -> str: ...


def episode_key_for(lineage_id: str, episode_id: str) -> str:
    """Deterministic memory-owned episode key (stable across replay)."""
    return f"iris-episode:{lineage_id}:{episode_id}"


def map_episode_source_to_graphiti_input(source: dict[str, object]) -> GraphitiEpisodeInput:
    """Deterministically map one accepted GraphitiEpisodeSource to the
    Graphiti adapter input. Pure function of the immutable source row —
    the same source always yields the same episode key/name/text/group."""
    episode_id = str(source["episodeId"])
    lineage_id = str(source["lineageId"])
    source_range = cast(dict[str, object], source["contextRange"])
    from_seq = cast(int, source_range["fromContextSeq"])
    to_seq = cast(int, source_range["toContextSeq"])
    temporal = cast(dict[str, object], source["temporal"])
    derivation = cast(dict[str, object], source["derivation"])
    source_unit_ids = [str(u) for u in cast(list[object], source["sourceUnitIds"])]
    memory_refs = [str(r) for r in cast(list[object], derivation.get("memoryRefs", []))]
    source_context_unit_ids = [
        str(u) for u in cast(list[object], derivation.get("sourceContextUnitIds", []))
    ]
    return GraphitiEpisodeInput(
        episode_key=episode_key_for(lineage_id, episode_id),
        name=f"Episode {episode_id}",
        text=str(source["canonicalContent"]),
        group=str(source["targetGroupId"]),
        timestamp=str(temporal["endedAt"]),
        source_context={
            "lineageId": lineage_id,
            "contextRange": {
                "fromContextSeq": from_seq,
                "toContextSeq": to_seq,
            },
            "sourceUnitIds": source_unit_ids,
            "isDerivedOnly": bool(source["isDerivedOnly"]),
            "derivation": {
                "memoryRefs": memory_refs,
                "sourceContextUnitIds": source_context_unit_ids,
            },
        },
    )


@dataclass(frozen=True, slots=True)
class IngestionResult:
    publication_id: str
    ingested: int
    failed: int
    skipped: int
    pending: int


def _update_publication_job(connection: sqlite3.Connection, publication_id: str, now: str) -> None:
    connection.execute(
        "UPDATE ingestion_jobs SET updated_at = ?, graphiti_status = "
        "CASE WHEN (SELECT COUNT(*) FROM accepted_episode_sources WHERE "
        "publication_id = ? AND graphiti_status != 'ingested') = 0 "
        "THEN 'ingested' ELSE 'partial' END "
        "WHERE publication_id = ?",
        (now, publication_id, publication_id),
    )


def ingest_publication_episodes(
    connection: sqlite3.Connection,
    publication_id: str,
    adapter: GraphitiAdapter,
) -> IngestionResult:
    """Ingest one publication's episode sources in source_position order.

    Idempotent + crash-safe: each source row is its own transaction
    (autocommit), ingested rows are skipped on replay, and a failure stops
    the batch with the failing row marked 'failed' (attempt recorded) —
    a retry resumes from the first non-ingested source without duplicating
    earlier episodes.
    """
    now = datetime.now(UTC).isoformat()
    rows = connection.execute(
        "SELECT episode_source_hash, source_position, episode_id, source_json, "
        "graphiti_status, attempt_count FROM accepted_episode_sources "
        "WHERE publication_id = ? AND graphiti_status != 'ingested' "
        "ORDER BY source_position",
        (publication_id,),
    ).fetchall()

    ingested = 0
    failed = 0
    skipped = 0
    for row in rows:
        source_hash, position, episode_id, source_json, status, attempt_count = row
        if status == "ingested":
            skipped += 1
            continue
        attempts = int(attempt_count) + 1
        connection.execute(
            "UPDATE accepted_episode_sources SET attempt_count = ?, "
            "graphiti_status = 'ingesting', updated_at = ? "
            "WHERE publication_id = ? AND episode_source_hash = ?",
            (attempts, now, publication_id, source_hash),
        )
        try:
            source = cast(dict[str, object], json.loads(source_json))
            episode_input = map_episode_source_to_graphiti_input(source)
            episode_key = adapter.add_episode(episode_input)
            connection.execute(
                "UPDATE accepted_episode_sources SET graphiti_status = 'ingested', "
                "graphiti_episode_key = ?, ingested_at = ?, last_error = NULL, "
                "updated_at = ? WHERE publication_id = ? AND episode_source_hash = ?",
                (episode_key, now, now, publication_id, source_hash),
            )
            ingested += 1
        except Exception as error:  # adapter failure: record and stop the batch
            connection.execute(
                "UPDATE accepted_episode_sources SET graphiti_status = 'failed', "
                "last_error = ?, updated_at = ? "
                "WHERE publication_id = ? AND episode_source_hash = ?",
                (str(error)[:1000], now, publication_id, source_hash),
            )
            failed += 1
            _update_publication_job(connection, publication_id, now)
            return IngestionResult(
                publication_id=publication_id,
                ingested=ingested,
                failed=failed,
                skipped=skipped,
                pending=0,
            )

    _update_publication_job(connection, publication_id, now)
    pending = int(
        connection.execute(
            "SELECT COUNT(*) FROM accepted_episode_sources "
            "WHERE publication_id = ? AND graphiti_status NOT IN ('ingested', 'failed')",
            (publication_id,),
        ).fetchone()[0]
    )
    return IngestionResult(
        publication_id=publication_id,
        ingested=ingested,
        failed=failed,
        skipped=skipped,
        pending=pending,
    )
