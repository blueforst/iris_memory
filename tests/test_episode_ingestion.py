"""iris_memory#11: deterministic Graphiti episode ingestion mapping.

The accepted GraphitiEpisodeSource rows map deterministically to the narrow
Graphiti adapter input; ordered ingestion is idempotent per
episode_source_hash, crash-resumable, and never loses the
Publication→episode mapping (attempts are recorded).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from iris_memory.acceptance import Accepted, accept_publication
from iris_memory.episodes import (
    GraphitiEpisodeInput,
    IngestionResult,
    episode_key_for,
    ingest_publication_episodes,
    map_episode_source_to_graphiti_input,
)

PUBLICATION_ID = "11111111-2222-4333-8444-555555555555"
LINEAGE = "identity-0123456789abcdef"


class RecordingAdapter:
    """Deterministic fake for the narrow Graphiti adapter (mock, explicitly
    marked — the real adapter stays behind the locked graphiti-core SDK)."""

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.calls: list[GraphitiEpisodeInput] = []
        self.fail_on = fail_on or set()

    def add_episode(self, episode_input: GraphitiEpisodeInput) -> str:
        if episode_input.episode_key in self.fail_on:
            raise RuntimeError("adapter failure")
        self.calls.append(episode_input)
        return episode_input.episode_key


def _seed_v3_publication(database_path: Path, key: str = "agent-run-v3-ingest") -> None:
    from test_acceptance_v3 import _episode_source, _publication, _request

    publication = _publication(
        episode_sources=[
            _episode_source("episode-1", 1, 2, "user: hello\nassistant: hi"),
            _episode_source("episode-2", 3, 3, "user: second"),
        ]
    )
    outcome = accept_publication(
        database_path, _request(idempotency_key=key, publication=publication)
    )
    assert isinstance(outcome, Accepted)


def test_deterministic_mapping_is_pure_and_stable() -> None:
    from test_acceptance_v3 import _episode_source

    source = _episode_source("episode-1", 1, 2, "user: hello\nassistant: hi")
    first = map_episode_source_to_graphiti_input(source)
    second = map_episode_source_to_graphiti_input(source)
    assert first == second
    assert first.episode_key == episode_key_for(LINEAGE, "episode-1")
    assert first.episode_key == "iris-episode:identity-0123456789abcdef:episode-1"
    assert first.group == f"group:{LINEAGE}"
    assert "hello" in first.text
    assert first.source_context["contextRange"] == {
        "fromContextSeq": 1,
        "toContextSeq": 2,
    }
    assert first.source_context["sourceUnitIds"] == ["u-1", "u-2"]
    assert first.source_context["isDerivedOnly"] is False


def test_ordered_ingestion_maps_every_source_in_position_order(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    _seed_v3_publication(database_path)
    adapter = RecordingAdapter()

    with sqlite3.connect(database_path) as connection:
        result = ingest_publication_episodes(connection, PUBLICATION_ID, adapter)

    assert isinstance(result, IngestionResult)
    assert result.ingested == 2 and result.failed == 0
    assert [c.episode_key for c in adapter.calls] == [
        f"iris-episode:{LINEAGE}:episode-1",
        f"iris-episode:{LINEAGE}:episode-2",
    ]

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT episode_id, graphiti_status, graphiti_episode_key, attempt_count "
            "FROM accepted_episode_sources ORDER BY source_position"
        ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        ("episode-1", "ingested"),
        ("episode-2", "ingested"),
    ]
    assert all(r[3] == 1 for r in rows)
    # the publication-level job flips to ingested
    with sqlite3.connect(database_path) as connection:
        job = connection.execute(
            "SELECT graphiti_status FROM ingestion_jobs WHERE publication_id = ?",
            (PUBLICATION_ID,),
        ).fetchone()
    assert job is not None and job[0] == "ingested"


def test_replay_is_idempotent_never_duplicates_episodes(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    _seed_v3_publication(database_path)
    adapter = RecordingAdapter()

    with sqlite3.connect(database_path) as connection:
        first = ingest_publication_episodes(connection, PUBLICATION_ID, adapter)
    with sqlite3.connect(database_path) as connection:
        second = ingest_publication_episodes(connection, PUBLICATION_ID, adapter)

    assert first.ingested == 2
    assert second.ingested == 0 and second.skipped == 0
    assert len(adapter.calls) == 2, "replay must never call add_episode again"


def test_crash_between_sources_resumes_without_duplication(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "router.sqlite3"
    _seed_v3_publication(database_path)
    adapter = RecordingAdapter(fail_on={f"iris-episode:{LINEAGE}:episode-1"})

    # First run: episode-1 fails, episode-2 never attempted (ordered stop).
    with sqlite3.connect(database_path) as connection:
        result = ingest_publication_episodes(connection, PUBLICATION_ID, adapter)
    assert result.ingested == 0 and result.failed == 1
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT graphiti_status, attempt_count, last_error FROM accepted_episode_sources "
            "WHERE publication_id = ? AND episode_id = 'episode-1'",
            (PUBLICATION_ID,),
        ).fetchone()
    assert row is not None and row[0] == "failed" and row[1] == 1
    assert row[2] is not None and "adapter failure" in row[2]

    # Simulate a crash-restart: NEW adapter, resume — episode-1 retried, then
    # episode-2 ingested; no episode is ever mapped twice.
    resume = RecordingAdapter()
    with sqlite3.connect(database_path) as connection:
        result = ingest_publication_episodes(connection, PUBLICATION_ID, resume)
    assert result.ingested == 2
    assert [c.episode_key for c in resume.calls] == [
        f"iris-episode:{LINEAGE}:episode-1",
        f"iris-episode:{LINEAGE}:episode-2",
    ]


def test_derived_only_source_keeps_anti_echo_provenance_in_mapping(
    tmp_path: Path,
) -> None:
    from test_acceptance_v3 import _episode_source, _publication, _request

    database_path = tmp_path / "data" / "router.sqlite3"
    publication = _publication(
        episode_sources=[
            _episode_source("episode-d", 1, 2, "recalled summary", derived=True, memrefs=["mem-1"])
        ],
        derived_only=True,
    )
    outcome = accept_publication(
        database_path, _request(idempotency_key="derived-run", publication=publication)
    )
    assert isinstance(outcome, Accepted)
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT source_json FROM accepted_episode_sources WHERE publication_id = ?",
            (PUBLICATION_ID,),
        ).fetchall()
    mapped = map_episode_source_to_graphiti_input(
        json.loads(str(rows[0][0]))  # type: ignore[index]
    )
    assert mapped.source_context["isDerivedOnly"] is True
    assert mapped.source_context["derivation"]["memoryRefs"] == ["mem-1"]


def test_episode_key_is_deterministic_identity_input() -> None:
    assert episode_key_for("l1", "e1") == "iris-episode:l1:e1"
    assert episode_key_for("l1", "e1") == episode_key_for("l1", "e1")
    assert episode_key_for("l1", "e1") != episode_key_for("l2", "e1")
