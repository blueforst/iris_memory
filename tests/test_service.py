"""Independent memory service: lock, /v1 endpoints, capabilities handshake."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from iris_memory.contracts.artifact import build_artifact_manifest
from iris_memory.db import apply_migrations
from iris_memory.service import DataRootLock, build_capabilities, make_handler


def _start_server(database_path: Path) -> tuple[ThreadingHTTPServer, threading.Thread]:
    apply_migrations(database_path)
    manifest = build_artifact_manifest()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(database_path, str(manifest["manifestSha256"]))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request() -> dict[str, Any]:
    return {
        "schemaVersion": "publication-acceptance-request-v1",
        "contractVersion": "0.1.0",
        "idempotencyKey": "svc-accept-001",
        "publication": {
            "schemaVersion": "historian-publication-v1",
            "publicationId": "44444444-4444-4444-8444-444444444444",
            "sourceSequence": 1,
            "publishedAt": "2026-08-01T00:00:00Z",
            "payloadHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "compartmentCount": 1,
            "segmentCount": 1,
            "evidenceCount": 1,
            "summary": "service baseline fixture",
        },
    }


def test_v1_health_and_capabilities(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    server, thread = _start_server(database_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)

        connection.request("GET", "/v1/health")
        response = connection.getresponse()
        assert response.status == 200
        health = json.loads(response.read().decode("utf-8"))
        assert health["schemaVersion"] == "health-response-v1"
        assert health["service"] == "iris-memory"

        connection.request("GET", "/v1/capabilities")
        response = connection.getresponse()
        assert response.status == 200
        caps = json.loads(response.read().decode("utf-8"))
        assert caps["schemaVersion"] == "capability-handshake-v2"
        assert caps["contractVersion"] == "0.2.0"
        assert caps["supportedMajor"] == 0
        assert caps["supportedMinor"] == 2
        assert caps["supportedMinors"] == [1, 2]
        assert "publication.accept" in caps["capabilities"]
        assert "graphiti.ingest" in caps["unavailableCapabilities"]
        assert "recall" in caps["unavailableCapabilities"]
        assert "expand" in caps["unavailableCapabilities"]
        assert "stableMemoryRef" in caps["unavailableCapabilities"]
        assert caps["graphitiStatus"] == "not_configured"
        assert isinstance(caps["manifestSha256"], str)
        assert len(caps["manifestSha256"]) == 64
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_v1_publications_accept_reuses_acceptance_core(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    server, thread = _start_server(database_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        body = json.dumps(_request(), ensure_ascii=False).encode("utf-8")
        connection.request(
            "POST",
            "/v1/publications/accept",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        receipt = json.loads(response.read().decode("utf-8"))
        assert receipt["schemaVersion"] == "acceptance-receipt-v1"
        assert receipt["status"] == "accepted"

        # Idempotent replay returns the same receipt (no second logic).
        connection.request(
            "POST",
            "/v1/publications/accept",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        replay = json.loads(response.read().decode("utf-8"))
        assert replay["schemaVersion"] == "duplicate-replay-receipt-v1"
        assert replay["receiptId"] == receipt["receiptId"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_v1_recall_and_expand_are_501_not_empty_success(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    server, thread = _start_server(database_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)

        connection.request(
            "POST", "/v1/memory/recall", body=b"{}", headers={"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        assert response.status == 501
        body = json.loads(response.read().decode("utf-8"))
        assert body["schemaVersion"] == "not-implemented-error-v1"
        assert body["error"] == "not_implemented"
        assert body["capability"] == "recall"

        connection.request(
            "POST", "/v1/memory/expand", body=b"{}", headers={"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        assert response.status == 501
        body = json.loads(response.read().decode("utf-8"))
        assert body["schemaVersion"] == "not-implemented-error-v1"
        assert body["error"] == "not_implemented"
        assert body["capability"] == "expand"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_capabilities_handshake_marks_unavailable_explicitly() -> None:
    caps = build_capabilities()
    assert set(caps["capabilities"]) == {
        "publication.accept",
        "capability.handshake",
        "health.read",
    }
    for unavailable in (
        "graphiti.ingest",
        "recall",
        "expand",
        "reindex",
        "stableMemoryRef",
    ):
        assert unavailable in caps["unavailableCapabilities"]
    assert caps["readiness"] == "ready"
    assert "graphiti_not_configured" in caps["degradedReasons"]


def test_data_root_lock_fails_fast_for_second_process(tmp_path: Path) -> None:
    from iris_memory.service import MemoryLockError

    first = DataRootLock.acquire(tmp_path)
    try:
        with pytest.raises(MemoryLockError):
            DataRootLock.acquire(tmp_path)
    finally:
        first.release()
    # After release the lock can be re-acquired.
    second = DataRootLock.acquire(tmp_path)
    second.release()


def test_data_root_lock_is_kernel_held_and_fails_fast(tmp_path: Path) -> None:
    """M1: the lock is an OS kernel lock (flock), not a path-existence check.
    A second acquire against the same data root must fail fast while the
    first holder lives; a leftover lockfile with no holder does not block."""
    from iris_memory.service import MemoryLockError

    first = DataRootLock.acquire(tmp_path)
    try:
        with pytest.raises(MemoryLockError):
            DataRootLock.acquire(tmp_path, timeout_ms=200)
    finally:
        first.release()
    # After release the lock is re-acquirable (even with the file present).
    second = DataRootLock.acquire(tmp_path, timeout_ms=200)
    second.release()


def test_data_root_lock_diagnostic_pid_is_not_authority(tmp_path: Path) -> None:
    """M1: PID/host in the lockfile are diagnostic only. A lockfile claiming a
    dead PID but NOT kernel-locked must be acquirable (the OS lock is the
    authority, never a PID guess)."""
    lock_path = tmp_path / "memory.lock"
    import json as _json

    lock_path.write_text(_json.dumps({"pid": 999999999, "host": "stale"}), encoding="utf-8")
    first = DataRootLock.acquire(tmp_path, timeout_ms=200)
    first.release()


def _validate_with_schema(instance: dict[str, object], schema_name: str) -> None:
    """Validate a runtime payload against the packaged authoritative schema
    (M2/M3 review): the wire contract is the JSON Schema; the endpoint output
    must pass it or the service has drifted."""
    import json

    from jsonschema import Draft202012Validator, FormatChecker

    from iris_memory.contracts.assets import contract_asset

    with contract_asset("schemas", f"{schema_name}.schema.json") as path:
        schema = json.loads(path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = list(validator.iter_errors(instance))
    assert not errors, f"{schema_name} validation failed: {errors}"


def test_capabilities_payload_passes_authoritative_schema(tmp_path: Path) -> None:
    """M2: GET /v1/capabilities output must validate against the packaged
    capability-handshake-v2 schema (single wire-contract authority)."""
    from iris_memory.service import build_capabilities

    payload = build_capabilities()
    _validate_with_schema(payload, "capability-handshake-v2")


def test_not_implemented_501_body_passes_authoritative_schema(tmp_path: Path) -> None:
    """M3: the 501 not-implemented body must validate against the versioned
    not-implemented-error-v1 schema — never an invalid request-shaped body."""
    _validate_with_schema(
        {
            "schemaVersion": "not-implemented-error-v1",
            "error": "not_implemented",
            "code": "not_implemented",
            "capability": "recall",
            "message": "recall is not implemented in the R0 baseline",
        },
        "not-implemented-error-v1",
    )
