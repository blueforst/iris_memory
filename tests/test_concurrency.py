"""Concurrency and crash-recovery hardening for Publication acceptance."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import time
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
    """Distinct idempotency keys for distinct publications all succeed under
    concurrency. Acceptance is unordered (source_sequence is UNIQUE-constrained,
    not predecessor-ordered); ordering semantics are out of scope here."""
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


def test_post_commit_hard_exit_durability(tmp_path: Path) -> None:
    """Post-commit hard-exit durability: a subprocess accepts, then hard-exits
    with os._exit(0) immediately after the commit returned (no clean
    interpreter shutdown). A restart sees the durable row and replay is
    idempotent. This is NOT a mid-commit crash test — the transaction
    completed before the exit."""
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
    BEGIN IMMEDIATE transaction serializes writers; exactly one Accepted.

    A common-start gate (ready file per process, then a single start file)
    guarantees the four subprocesses are all alive and past import before
    any of them enters the transaction, so a genuine write-write overlap is
    exercised rather than sequential replay."""
    database_path = tmp_path / "router.sqlite3"
    apply_migrations(database_path)
    db_repr = repr(str(database_path))
    request_repr = repr(_request())
    ready_dir = tmp_path / "ready"
    start_file = tmp_path / "start"
    ready_dir.mkdir()
    script = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        f'ready = Path({repr(str(ready_dir))}) / f"{{os.getpid()}}.ready"\n'
        f"start = Path({repr(str(start_file))})\n"
        f"db = Path({db_repr})\n"
        "from iris_memory.acceptance import accept_publication\n"
        "ready.write_text('ready')\n"
        "deadline = time.time() + 15\n"
        "while not start.exists():\n"
        "    if time.time() > deadline:\n"
        "        print('START_TIMEOUT')\n"
        "        sys.exit(2)\n"
        "    time.sleep(0.005)\n"
        f"outcome = accept_publication(db, {request_repr})\n"
        "print(type(outcome).__name__)\n"
    )
    # Spawn all four processes; each reports ready before the gate opens.
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    deadline = time.monotonic() + 15
    while len(list(ready_dir.glob("*.ready"))) < 4:
        if time.monotonic() > deadline:
            for proc in procs:
                proc.kill()
            raise AssertionError("subprocesses did not all report ready")
        time.sleep(0.005)
    # Open the common start gate: every process is alive and past import.
    start_file.write_text("go")
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


def test_same_key_different_payload_concurrent_conflicts(tmp_path: Path) -> None:
    """Competition matrix: same idempotency key submitted concurrently with
    different payloads. BEGIN IMMEDIATE serializes; exactly one Accepts and
    every other thread gets a typed IdempotencyConflict (no key persisted for
    the losers beyond the winner's row)."""
    database_path = tmp_path / "router.sqlite3"
    apply_migrations(database_path)
    barrier = threading.Barrier(4)

    def submit(variant: int) -> str:
        barrier.wait()
        req = _request()
        req["publication"] = {
            **req["publication"],
            "publicationId": f"3333333{variant}-3333-4333-8333-33333333333{variant}",
            "summary": f"same-key different-payload variant {variant}",
        }
        return type(accept_publication(database_path, req)).__name__

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = [f.result() for f in [executor.submit(submit, i) for i in range(4)]]
    assert results.count("Accepted") == 1
    assert results.count("IdempotencyConflict") == 3
    assert _counts(database_path) == {
        "accepted_publications": 1,
        "publication_idempotency": 1,
        "acceptance_receipts": 1,
        "ingestion_jobs": 1,
    }


def test_same_publication_different_key_concurrent(tmp_path: Path) -> None:
    """Competition matrix: the same publication_id submitted concurrently with
    different idempotency keys. Exactly one Accepts (first writer wins); the
    others replay or conflict deterministically, and the ledger never holds
    more than one accepted row for the publication."""
    database_path = tmp_path / "router.sqlite3"
    apply_migrations(database_path)
    barrier = threading.Barrier(4)

    def submit(key_variant: int) -> str:
        barrier.wait()
        req = _request()
        req["idempotencyKey"] = f"pub-key-{key_variant}"
        return type(accept_publication(database_path, req)).__name__

    def submit_with_receipt(key_variant: int) -> tuple[str, str | None]:
        barrier.wait()
        req = _request()
        req["idempotencyKey"] = f"pub-key-{key_variant}"
        outcome = accept_publication(database_path, req)
        receipt_id: str | None = None
        if isinstance(outcome, (Accepted, DuplicateReplay)):
            receipt_id = str(outcome.receipt["receiptId"])
        return type(outcome).__name__, receipt_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = [f.result() for f in [executor.submit(submit_with_receipt, i) for i in range(4)]]
    statuses = [r[0] for r in results]
    # The same publication + identical payload with different keys must
    # converge deterministically: exactly one Accepted and the other three
    # exact alternate-key replays, all sharing ONE receipt id (review
    # blocker #2, round 2 review).
    assert statuses.count("Accepted") == 1, statuses
    assert statuses.count("DuplicateReplay") == 3, statuses
    receipt_ids = {r[1] for r in results if r[1] is not None}
    assert len(receipt_ids) == 1, receipt_ids
    accepted = _counts(database_path)
    assert accepted["accepted_publications"] == 1
    assert accepted["acceptance_receipts"] == 1
    assert accepted["ingestion_jobs"] == 1
    # Every one of the four distinct keys was persisted (the accepted key
    # plus the three exact alternate-key replays).
    assert accepted["publication_idempotency"] == 4, accepted


def test_same_source_sequence_different_publication_concurrent(tmp_path: Path) -> None:
    """Competition matrix: two different publications with the SAME
    source_sequence submitted concurrently. source_sequence is UNIQUE; exactly
    one Accepts and the other gets a typed SequenceConflict. This locks the
    UNIQUE-constraint semantics (not ordered acceptance)."""
    database_path = tmp_path / "router.sqlite3"
    apply_migrations(database_path)
    barrier = threading.Barrier(2)

    def submit(pub_variant: int) -> str:
        barrier.wait()
        req = _request()
        req["idempotencyKey"] = f"seq-key-{pub_variant}"
        req["publication"] = {
            **req["publication"],
            "publicationId": f"4444444{pub_variant}-4444-4444-8444-44444444444{pub_variant}",
            "summary": f"same-sequence different-publication variant {pub_variant}",
        }
        return type(accept_publication(database_path, req)).__name__

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [f.result() for f in [executor.submit(submit, i) for i in range(2)]]
    assert results.count("Accepted") == 1, results
    assert results.count("SequenceConflict") == 1, results
    accepted = _counts(database_path)
    assert accepted["accepted_publications"] == 1
    assert accepted["acceptance_receipts"] == 1
    assert accepted["ingestion_jobs"] == 1


def test_alternate_key_replay_vs_key_reuse_competitive(tmp_path: Path) -> None:
    """Competition matrix: after P(K1) is accepted, an exact alternate-key
    replay P(K2) races a different-publication reuse Q(K2) of that same key.

    Legal outcomes (both must be locked):
      - P(K2) binds first  -> DuplicateReplay(P) + IdempotencyConflict(Q),
                             accepted publications = 1
      - Q(K2) binds first  -> Accepted(Q) + IdempotencyConflict(P replay),
                             accepted publications = 2

    In both cases: one key has exactly one binding, P's original receipt is
    unchanged, and there is no half-accept or silent overwrite."""
    database_path = tmp_path / "router.sqlite3"
    apply_migrations(database_path)

    # Establish the prior state: accept P(K1) first.
    first = _request()  # publication P with key K1 ("concurrent-key")
    first_outcome = accept_publication(database_path, first)
    assert isinstance(first_outcome, Accepted)
    first_receipt = str(first_outcome.receipt["receiptId"])

    alt_p = dict(first)
    alt_p["idempotencyKey"] = "alt-key-for-pub"  # P(K2): exact alternate replay

    reuse_q = dict(first)
    reuse_q["idempotencyKey"] = "alt-key-for-pub"  # Q(K2): same key, different pub
    reuse_q["publication"] = {
        **reuse_q["publication"],
        "publicationId": "55555555-5555-4555-8555-555555555555",
        "sourceSequence": 7,
        "summary": "reuse of alt key with a different publication",
    }

    for attempt in range(8):  # run the race repeatedly to cover both winners
        db = tmp_path / f"router-race-{attempt}.sqlite3"
        apply_migrations(db)
        out1 = accept_publication(db, first)
        assert isinstance(out1, Accepted)
        barrier = threading.Barrier(2)

        def run_alt(db_arg: Path = db, barrier_arg: threading.Barrier = barrier) -> str:
            barrier_arg.wait()
            return type(accept_publication(db_arg, alt_p)).__name__

        def run_reuse(db_arg: Path = db, barrier_arg: threading.Barrier = barrier) -> str:
            barrier_arg.wait()
            return type(accept_publication(db_arg, reuse_q)).__name__

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [f.result() for f in [executor.submit(run_alt), executor.submit(run_reuse)]]
        statuses = sorted(results)

        if statuses == ["DuplicateReplay", "IdempotencyConflict"]:
            # P(K2) bound first: exact replay, Q conflicts.
            assert _counts(db)["accepted_publications"] == 1
        elif statuses == ["Accepted", "IdempotencyConflict"]:
            # Q(K2) bound first: Q accepted, P(K2) replay conflicts.
            assert _counts(db)["accepted_publications"] == 2
        else:
            raise AssertionError(f"unexpected race outcome {statuses}")

        # One key -> exactly one binding; P's receipt unchanged in every case.
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM publication_idempotency WHERE idempotency_key = ?",
                ("alt-key-for-pub",),
            ).fetchone()
            assert rows is not None and rows[0] == 1, "alt key must have exactly one binding"
            p_receipt = conn.execute(
                "SELECT receipt_id FROM accepted_publications WHERE publication_id = ?",
                (str(first["publication"]["publicationId"]),),
            ).fetchone()
            assert p_receipt is not None
            assert str(p_receipt[0]) == first_receipt, "P's receipt must be unchanged"
