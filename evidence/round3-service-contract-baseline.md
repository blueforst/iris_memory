# R0 Service Contract Baseline Evidence (Round 3)

Date: 2026-08-02
Baseline: f160b45 (blueforst/iris_memory main, second-round merge)
Branch: memory/r0-service-contract-baseline
Environment: mcp-remote (117.72.194.243), Python 3.12, uv 0.11.32

## Scope

Round 3 wraps the existing contracts + acceptance core into a runnable,
verifiable service protocol baseline:

- deterministic contract artifact (manifest + schemas + fixtures + OpenAPI +
  checksums), built from the REAL asset directories (no second hand-written
  list);
- an independent long-lived memory service process with its own data root,
  lock, migrations and shutdown;
- capability/version handshake; Publication acceptance API reusing the tested
  acceptance core;
- Graphiti, Recall, Expand, reindex and stable memoryRef remain explicitly
  unavailable.

## Deterministic contract artifact

`iris_memory/contracts/artifact.py` scans the packaged asset directories
(`schemas/*.schema.json`, `fixtures/*.json`, `openapi/*.json`) and builds a
manifest with per-file SHA-256 checksums and a `manifestSha256` covering the
canonical manifest bytes (sorted keys, compact separators, `ensure_ascii`).
Verification recomputes the hash, cross-checks the lists against the real
directories and re-validates every valid/invalid fixture.

Commands (run locally):

```text
uv run --with jsonschema==4.26.0 iris-memory contract build --output-dir artifacts/iris-memory-contracts-0.1.1
uv run --with jsonschema==4.26.0 iris-memory contract verify --manifest artifacts/.../manifest.json --dir artifacts/...
```

Result: artifact written; manifestSha256 =
`2cb22deb5efded5a112dbb38c19506e6185ad328a973f7a96d9e66faf59a761b` (contract 0.1.1, capability-handshake-v2).
Build is byte-reproducible (two builds diff as identical). CI gains an
`artifact` job that builds into `$RUNNER_TEMP` and verifies the unpacked
artifact in place, plus a byte-reproducibility diff.

## Independent memory service process

`iris_memory/service.py`:

```text
iris-memory serve
-> acquire <data-root>/memory.lock (kernel-held flock/msvcrt, persistent inode, full lifetime)
-> apply migrations (checksum-verified)
-> start loopback HTTP (default 127.0.0.1:18011)
-> report health/capabilities
-> remain alive until interrupted; lock released on shutdown
```

A second process against the same data root fails fast (`MemoryLockError`; the OS lock is authoritative, PID is diagnostic only).
Endpoints:

```text
GET  /v1/health
GET  /v1/capabilities
POST /v1/publications/accept
POST /v1/memory/recall    -> 501 recall_not_implemented
POST /v1/memory/expand    -> 501 expand_not_implemented
```

The Publication endpoint calls the same `accept_publication()` core tested in
rounds 1-2 — no second idempotency implementation. Replay returns the same
receipt.

## Capability/version handshake

`GET /v1/capabilities` returns (capability-handshake-v2):

```text
serviceVersion, contractPackage, contractVersion, contractVersions,
supportedMajor=0, supportedMinor=1, manifestSha256, schemaCount=14,
fixtureCount=30, capabilities=[publication.accept, capability.handshake,
health.read], unavailableCapabilities=[graphiti.ingest, recall, expand,
reindex, stableMemoryRef], graphitiStatus=not_configured,
readiness=ready, degradedReasons=[graphiti_not_configured,
recall_not_implemented, expand_not_implemented]
```

Graphiti is not configured; Publication durable acceptance is ready, but the
service never claims a full Memory system ready. Recall/Expand return 501 with
a versioned error — never an empty success.

## Migration and runtime reliability

`iris_memory/db/migrate.py` now records a SHA-256 checksum per applied
migration:

- an applied migration whose SQL changed fails closed
  (`MigrationChecksumError`);
- migration failure rolls back atomically (statements + bookkeeping);
- re-running is idempotent and keeps checksum records;
- backfill adds the checksum column to pre-round-3 databases.

Migration checksum bookkeeping is introduced by forward migration 0004 (no runtime ALTER); 0001-0003 are unchanged.

## Verification

```text
uvx ruff==0.15.22 check .           -> All checks passed!
uvx ruff==0.15.22 format --check .  -> 25 files already formatted
uvx mypy==2.3.0                     -> Success: no issues found in 17 source files
uv run --with pytest==9.1.1 --with jsonschema==4.26.0 pytest
                                    -> 69 passed (40 round-2 + 9 artifact
                                       + 9 service + 8 migrations + 3 process)
```

New tests: `tests/test_artifact.py` (9), `tests/test_service.py` (9, incl.
schema-validated handshake + 501 bodies), `tests/test_migrations.py` (8, incl.
release-manifest strict checks and single-apply legacy backfill), and
`tests/test_service_process.py` (3, real serve subprocess incl. graceful
shutdown exit-code assertion).

## Cross-repo compatibility

The built artifact (manifest + 14 schemas + 30 fixtures + OpenAPI, contract 0.1.1 / capability-handshake-v2) is
committed in the iris-agent repo under `fixtures/memory-contracts-artifact/`
for the agent-side compatibility gate (manifest hash recomputation, schema/
fixture list agreement, valid/invalid fixture agreement, major-version
fail-closed). Dependency direction stays fixed: iris-memory publishes the
artifact; iris-agent pins and verifies it.

## Known gaps (unchanged by this round)

- No Publication delivery/ACK (full Historian/outbox is a later milestone);
- Graphiti/Neo4j ingestion, stable memoryRef, Recall/Search/Expand business
  and reindex remain explicitly not implemented;
- No npm/PyPI publication (user authorization required).

## Independent review fixes (M1-M6)

Audited head `571de1d` by an independent reviewer; all merge blockers fixed:

- **M1 — kernel-held OS lock**: `DataRootLock` now uses `fcntl.flock` (POSIX)
  / `msvcrt.locking` (Windows) as the authoritative OS lock with a short
  acquire timeout (second service process fails fast). PID/host in the
  lockfile are DIAGNOSTIC ONLY and never used for stale reaping; a leftover
  lockfile with no kernel holder does not block a fresh acquire. New tests:
  kernel-lock fail-fast, PID-not-authority. Real subprocess tests prove
  second-process rejection, hard-exit (SIGKILL) restart with kernel auto-
  release, and graceful reopen.
- **M2 — handshake ↔ schema alignment**: `capability-handshake-v2.schema.json`
  now describes the REAL runtime payload (schemaVersion/serviceName/
  serviceVersion/contractPackage/contractVersion/contractVersions/
  supportedMajor/supportedMinor/manifestSha256/schemaCount/fixtureCount/
  capabilities incl. capability.handshake/unavailableCapabilities/
  graphitiStatus/readiness/degradedReasons); valid + invalid-semver fixtures
  updated. A new test validates the live `GET /v1/capabilities` output against
  the authoritative schema (CI would have caught the drift).
- **M3 — versioned 501**: new `not-implemented-error-v1` schema + fixtures;
  recall/expand 501 bodies use it (`schemaVersion/error/code/capability`),
  validated by test against the schema. OpenAPI documents the /v1 surface.
- **M4 — forward-migration checksum**: new `0004_checksum_metadata.sql`
  forward migration adds the checksum column (no runtime ALTER); the runner
  backfills legacy empty checksums ONLY from the release-owned
  `db/migrations/checksums.json` manifest (never current disk bytes), fails
  closed when no release checksum exists, and fails closed on a recorded
  version missing from the source set. 0001-0003 unchanged.
- **M5 — product-path subprocess evidence**: `tests/test_service_process.py`
  exercises the real `iris-memory serve` lifecycle (ready/health/
  capabilities, Publication replay, second-process lock rejection, SIGKILL
  hard-exit restart, graceful shutdown + immediate reopen).
- **M6 — self-contained artifact verification**: `verify_artifact_directory`
  is fully root-authoritative — manifest structure vs declared lists,
  per-file content hashes, EXTRA-file rejection (complete artifact = exact
  manifest surface), and fixture re-validation using the artifact's OWN
  schemas (a consumer with only the artifact reaches the same verdict).
  `write_contract_artifact` refuses a non-empty output directory.

Test count: 68 pytest (round-2 40 + artifact 9 + service 9 + migrations 8 +
process 3 - legacy 1 merged). ruff/mypy/format clean.
