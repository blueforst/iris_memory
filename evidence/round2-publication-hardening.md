# R1 / R0 Publication Hardening Evidence (Round 2)

Date: 2026-08-02
Baseline: b80c298 (blueforst/iris_memory main, first-round merge)
Branch: round2/publication-hardening
Environment: mcp-remote (117.72.194.243), Python 3.12, uv 0.11.32

## Concurrency and crash-recovery hardening

New test file `tests/test_concurrency.py` (5 tests) exercises Publication
acceptance under contention and crash:

- 8 threads submit the identical publication, exactly one `Accepted`, the
  other seven `DuplicateReplay` with the same receipt id, ledger ends with
  exactly one row per table (BEGIN IMMEDIATE serializes writers).
- 4 threads submit distinct keys/publications: all `Accepted`, ordering kept
  by source_sequence.
- After a concurrent burst, reusing the consumed key with different content
  is classified `IdempotencyConflict`.
- Crash-during-commit: SQLite autocommit durability means a restart sees
  exactly one accepted publication and idempotent replay works.
- 4 independent OS subprocesses race the same key: exactly one `Accepted`,
  three `DuplicateReplay` (cross-process idempotency).

## Verification

```text
uvx ruff==0.15.22 check .        -> All checks passed!
uvx ruff==0.15.22 format --check . -> 23 files already formatted
uvx mypy==2.3.0                  -> Success: no issues found in 15 source files (pyproject [tool.mypy] files=["src"]; tests not in mypy scope, consistent with round 1)
uv run --with pytest==9.1.1 --with jsonschema==4.26.0 pytest
                                 -> 38 passed in 1.72s
```

Concurrency tests re-run 5x with no flake (default sqlite3 5s busy timeout covers BEGIN IMMEDIATE contention).

The full suite is 38 tests (29 first-round + 9 new hardening tests).

Competition matrix added: same key / different payload concurrent (1 Accepted
+ 3 IdempotencyConflict), same publication / different key concurrent (1
Accepted + 3 exact alternate-key replays + 4 idempotency rows + 1 receipt id),
same source_sequence / different publication concurrent (1 Accepted + 1
SequenceConflict), and alternate-key replay vs key reuse competitive. The
cross-process test imports acceptance BEFORE the ready file, so the common
ready/start gate opens only after all four processes completed import: this is
a synchronized post-import start / contention attempt (it does not claim to
prove OS-level overlap inside the BEGIN IMMEDIATE transaction — that would
require a fault-injection hook inside the production function). The crash test is accurately
named post-commit hard-exit durability (the transaction completes before
os._exit).

## Still not satisfied

Real Graphiti/Neo4j ingestion, stable memoryRef/RecallDisposition,
recall/expand business implementation, reindex, backup/restore, and the full
GraphitiProfile lock remain deferred. This evidence does not mark R0/R4
complete and does not update the accepted Notion Roadmap percentage.