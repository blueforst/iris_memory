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
uv run --with jsonschema==4.26.0 iris-memory contract build --output-dir artifacts/iris-memory-contracts-0.1.0
uv run --with jsonschema==4.26.0 iris-memory contract verify --manifest artifacts/.../manifest.json --dir artifacts/...
```

Result: artifact written; manifestSha256 =
`e0a0983958d75a755b0c717e0e2ea38280829ceb3c961b6b6865d4afb631594f`.
Build is byte-reproducible (two builds diff as identical). CI gains an
`artifact` job that builds into `$RUNNER_TEMP` and verifies the unpacked
artifact in place, plus a byte-reproducibility diff.

## Independent memory service process

`iris_memory/service.py`:

```text
iris-memory serve
-> acquire <data-root>/memory.lock (O_EXCL, full lifetime)
-> apply migrations (checksum-verified)
-> start loopback HTTP (default 127.0.0.1:18011)
-> report health/capabilities
-> remain alive until interrupted; lock released on shutdown
```

A second process against the same data root fails fast (`FileExistsError`).
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

`GET /v1/capabilities` returns (capability-handshake-v1):

```text
serviceVersion, contractPackage, contractVersion, contractVersions,
supportedMajor=0, supportedMinor=1, manifestSha256, schemaCount=13,
fixtureCount=28, capabilities=[publication.accept, capability.handshake,
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

Existing 0001-0003 migrations are unchanged (forward-only; no edits).

## Verification

```text
uvx ruff==0.15.22 check .           -> All checks passed!
uvx ruff==0.15.22 format --check .  -> 25 files already formatted
uvx mypy==2.3.0                     -> Success: no issues found in 17 source files
uv run --with pytest==9.1.1 --with jsonschema==4.26.0 pytest
                                    -> 57 passed (40 round-2 + 9 artifact
                                       + 5 service + 3 migration checksum)
```

New tests: `tests/test_artifact.py` (9), `tests/test_service.py` (5), and 3
migration checksum/reliability tests appended to `tests/test_migrations.py`.

## Cross-repo compatibility

The built artifact (manifest + 13 schemas + 28 fixtures + OpenAPI) is
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
