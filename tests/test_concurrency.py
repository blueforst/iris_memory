"""Concurrency and crash-recovery hardening for Publication acceptance."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from iris_memory.acceptance import (
    Accepted,
    DuplicateReplay,
    accept_publication,
)
from iris_memory.db import apply_migrations

PUBLICATION_ID = "11111111-1111-4111-8111-111111111111"
HASH_64 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _request(idempotency_key: str = "concurrent-key") -> dict[str, Any]:
    return {
        "schemaVersion": "publication-acceptance-request-v1",
        "contractVersion": "0.1.0",
        "idempotencyKey": idempotency_key,
        "publication": {
            "schemaVersion": "historian-publication-v1",
            "publicationId": PUBLICATION_ID,
            "sourceSequence": 1,
            "publishedAt": "2026-08-01T00:00:00Z",
            "payloadHash": HASH_64,
            "compartmentCount": 1,
            "segmentCount": 1,
            "evidenceCount": 1,
            "summary": "concurrent acceptance fixture",
        },
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


def test_concurrent_same_key_threads_accept_exactly_once(tmp_path: Path) -> None:
    """N threads submit the identical publication; exactly one is Accepted and
    every other thread observes a deterministic DuplicateReplay with the same
    receipt. The ledger must end with exactly one row in each table."""
    database_path = tmp_path / "router.sqlite3"
    apply_migrations(database_path)
    request = _request()
    barrier = threading.Barrier(8)

    def submit() -> tuple[str, str | None]:
        barrier.wait()
        outcome = accept_publication(database_path, request)
        if isinstance(outcome, Accepted):
            return "accepted", str(outcome.receipt["receiptId"])
        if isinstance(outcome, DuplicateReplay):
            return "duplicate_replay", str(outcome.receipt["receiptId"])
        return type(outcome).__name__, None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: submit(), range(8)))

    accepted = [r for r in results if r[0] == "accepted"]
    duplicates = [r for r in results if r[0] == "duplicate_replay"]
    assert len(accepted) == 1
    assert len(duplicates) == 7
    receipt_ids = {r[1] for r in results if r[1] is not None}
    assert len(receipt_ids) == 1
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 1,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_concurrent_distinct_keys_both_accepted(tmp_path: Path) -> None:
    """Distinct idempotency keys for distinct publications both succeed under
    concurrency; ordering is preserved by source_sequence."""
    database_path = tmp_path / "router.sqlite3"
    apply_migrations(database_path)
    barrier = threading.Barrier(4)

    def submit(key: str, publication_id: str, sequence: int) -> str:
        barrier.wait()
        outcome = accept_publication(
            database_path,
            {
                "schemaVersion": "publication-acceptance-request-v1",
                "contractVersion": "0.1.0",
                "idempotencyKey": key,
                "publication": {
                    "schemaVersion": "historian-publication-v1",
                    "publicationId": publication_id,
                    "sourceSequence": sequence,
                    "publishedAt": "2026-08-01T00:00:00Z",
                    "payloadHash": HASH_64,
                    "compartmentCount": 1,
                    "segmentCount": 1,
                    "evidenceCount": 1,
                    "summary": f"concurrent distinct {key}",
                },
            },
        )
        return type(outcome).__name__

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(
                submit,
                f"key-{i}",
                f"0000000{i}-0000-4000-8000-00000000000{i}",
                i + 1,
            )
            for i in range(1, 5)
        ]
        results = [f.result() for f in futures]

    assert all(r == "Accepted" for r in results)
    assert _counts(database_path) == {
        "accepted_publications": 4,
        "publication_idempotency": 4,
        "acceptance_receipts": 4,
        "ingestion_jobs": 4,
    }


def test_same_key_reused_with_different_publication_after_concurrency(
    tmp_path: Path,
) -> None:
    """After a concurrent burst, reusing the consumed key with different
    content must be classified as a conflict, not accepted or replayed."""
    database_path = tmp_path / "router.sqlite3"
    apply_migrations(database_path)
    request = _request()
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: accept_publication(database_path, request), range(4)))

    reused = {
        **request,
        "publication": {
            **request["publication"],
            "publicationId": "22222222-2222-4222-8222-222222222222",
            "sourceSequence": 2,
            "summary": "different publication reusing concurrent key",
        },
    }
    outcome = accept_publication(database_path, reused)
    assert type(outcome).__name__ == "IdempotencyConflict"
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 1,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_crash_during_commit_recovers_consistently(tmp_path: Path) -> None:
    """A real crash after a durable commit: a subprocess accepts, then is
    killed with os._exit(0) immediately after the transaction committed.
    On restart the committed row is visible and replay is idempotent."""
    database_path = tmp_path / "router.sqlite3"
    apply_migrations(database_path)
    db_repr = repr(str(database_path))
    request_repr = repr(_request())
    script = (
        "import os\n"
        "from iris_memory.acceptance import accept_publication\n"
        "from pathlib import Path\n"
        f"outcome = accept_publication(Path({db_repr}), {request_repr})\n"
        "print(type(outcome).__name__, flush=True)\n"
        "os._exit(0)\n"
    )
    # The subprocess hard-exits without clean interpreter shutdown, like a
    # process kill right after the commit returned.
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "Accepted"

    # A fresh process (restart) sees the durable row and replays idempotently.
    replay = accept_publication(database_path, _request())
    assert isinstance(replay, DuplicateReplay)
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 1,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_subprocess_accept_parallel_same_key(tmp_path: Path) -> None:
    """Cross-process concurrency: several OS processes race the same key. The
    BEGIN IMMEDIATE transaction serializes writers; exactly one Accepted."""
    database_path = tmp_path / "router.sqlite3"
    apply_migrations(database_path)
    db_repr = repr(str(database_path))
    request_repr = repr(_request())
    script = (
        "from iris_memory.acceptance import accept_publication\n"
        "from pathlib import Path\n"
        f"outcome = accept_publication(Path({db_repr}), {request_repr})\n"
        "print(type(outcome).__name__)\n"
    )
    # Spawn all four processes before waiting, so they genuinely race.
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    statuses = []
    for proc in procs:
        out, _ = proc.communicate()
        if proc.returncode == 0:
            statuses.append(out.strip())
    assert len(statuses) == 4
    assert statuses.count("Accepted") == 1
    assert statuses.count("DuplicateReplay") == 3
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 1,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }
