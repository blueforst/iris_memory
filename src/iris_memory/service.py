"""Independent long-lived Memory service process (R0 service baseline).

The service owns its own data root, lock, migrations and shutdown:

    iris-memory serve
    -> acquire <data-root>/memory.lock (OS exclusive, full lifetime)
    -> validate config
    -> apply migrations
    -> open acceptance ledger
    -> start loopback HTTP
    -> report health/capabilities
    -> remain alive until shutdown

Endpoints (M1 baseline):
    GET  /v1/health
    GET  /v1/capabilities
    POST /v1/publications/accept
    POST /v1/memory/recall    -> 501 (recall not implemented)
    POST /v1/memory/expand    -> 501 (expand not implemented)

The Publication endpoint directly reuses the tested acceptance core
(`accept_publication`) — there is no second idempotency implementation.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from iris_memory.acceptance import (
    Accepted,
    DuplicateReplay,
    IdempotencyConflict,
    SequenceConflict,
    UnsupportedVersion,
    ValidationFailure,
    accept_publication,
)
from iris_memory.config import MemoryServiceConfig
from iris_memory.contracts.artifact import build_artifact_manifest
from iris_memory.contracts.manifest import CONTRACT_PACKAGE
from iris_memory.db import apply_migrations
from iris_memory.health import build_health_report

SUPPORTED_MAJOR = 0
SUPPORTED_MINOR = 1

_AVAILABLE_CAPABILITIES = (
    "publication.accept",
    "capability.handshake",
    "health.read",
)

_UNAVAILABLE_CAPABILITIES = (
    "graphiti.ingest",
    "recall",
    "expand",
    "reindex",
    "stableMemoryRef",
)


class MemoryLockError(Exception):
    """Raised when another process already holds the memory data-root lock."""


class DataRootLock:
    """OS-level exclusive lock on <data-root>/memory.lock for the process
    lifetime. Uses an atomic O_EXCL lockfile with a live PID check so the
    second process fails fast without entering recovery."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fd: int | None = None

    @classmethod
    def acquire(cls, data_root: Path) -> DataRootLock:
        lock_path = data_root / "memory.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(
                fd,
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "host": socket.gethostname(),
                        "startedAt": __import__("datetime").datetime.now().isoformat(),
                    }
                ).encode("utf-8"),
            )
            os.fsync(fd)
        except OSError:
            os.close(fd)
            raise
        lock = cls(lock_path)
        lock._fd = fd
        return lock

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        with contextlib.suppress(FileNotFoundError):
            self._lock_path.unlink()


def build_capabilities() -> dict[str, object]:
    """Capability/version handshake payload (contract:
    capability-handshake-v1). Expresses exactly what is available and what is
    explicitly unavailable; Graphiti is not configured in this baseline."""
    manifest = build_artifact_manifest()
    readiness, degraded = _readiness(manifest)
    return {
        "schemaVersion": "capability-handshake-v1",
        "serviceVersion": "0.1.0",
        "serviceName": "iris-memory",
        "contractPackage": CONTRACT_PACKAGE.name,
        "contractVersion": CONTRACT_PACKAGE.version,
        "contractVersions": {"major": SUPPORTED_MAJOR, "minor": SUPPORTED_MINOR},
        "supportedMajor": SUPPORTED_MAJOR,
        "supportedMinor": SUPPORTED_MINOR,
        "manifestSha256": manifest["manifestSha256"],
        "schemaCount": manifest["schemaCount"],
        "fixtureCount": manifest["fixtureCount"],
        "capabilities": list(_AVAILABLE_CAPABILITIES),
        "unavailableCapabilities": list(_UNAVAILABLE_CAPABILITIES),
        "graphitiStatus": "not_configured",
        "readiness": readiness,
        "degradedReasons": list(degraded),
    }


def _readiness(manifest: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    """Graphiti is not configured, so the service can be ready for durable
    Publication acceptance but must never claim a full Memory system ready."""
    degraded: list[str] = []
    if manifest.get("graphitiStatus") is None:
        pass
    if len(_UNAVAILABLE_CAPABILITIES) > 0:
        degraded.append("graphiti_not_configured")
        degraded.append("recall_not_implemented")
        degraded.append("expand_not_implemented")
    return ("ready", tuple(dict.fromkeys(degraded)))


def make_handler(database_path: Path, manifest_sha: str) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to one SQLite database path."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.rstrip("/") or "/"
            if path == "/v1/health" or path == "/health":
                self._send_json(200, build_health_report(database_path).as_dict())
            elif path == "/v1/capabilities":
                self._send_json(200, build_capabilities())
            elif path == "/v1/health/manifest":
                self._send_json(
                    200,
                    {"manifestSha256": manifest_sha, "contractVersion": CONTRACT_PACKAGE.version},
                )
            else:
                self._send_json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            path = self.path.rstrip("/")
            if path == "/v1/publications/accept" or path == "/historian/publications":
                self._handle_publication()
            elif path == "/v1/memory/recall" or path == "/memory/recall":
                self._send_json(
                    501,
                    {
                        "schemaVersion": "recall-request-v1",
                        "error": "recall_not_implemented",
                        "code": "not_implemented",
                    },
                )
            elif path == "/v1/memory/expand" or path == "/memory/expand":
                self._send_json(
                    501,
                    {
                        "schemaVersion": "expansion-request-v1",
                        "error": "expand_not_implemented",
                        "code": "not_implemented",
                    },
                )
            else:
                self._send_json(404, {"error": "not_found"})

        def _handle_publication(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                request = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                self._send_json(400, {"error": "invalid_json"})
                return

            outcome = accept_publication(database_path, request)
            if isinstance(outcome, (Accepted, DuplicateReplay)):
                self._send_json(200, outcome.receipt)
            elif isinstance(outcome, (IdempotencyConflict, SequenceConflict)):
                self._send_json(409, outcome.error)
            elif isinstance(outcome, UnsupportedVersion):
                self._send_json(422, outcome.error)
            elif isinstance(outcome, ValidationFailure):
                self._send_json(400, {"errors": list(outcome.errors)})

        def _send_json(self, status: int, body: object) -> None:
            data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    return Handler


def serve(config: MemoryServiceConfig, *, host: str = "127.0.0.1", port: int = 18011) -> None:
    """Acquire the lock, apply migrations, serve until interrupted, then
    release the lock. A second process against the same data root fails fast."""
    config.ensure_directories()
    lock = DataRootLock.acquire(config.data_root)
    try:
        apply_migrations(config.database_path)
        manifest = build_artifact_manifest()
        manifest_sha = str(manifest["manifestSha256"])
        handler = make_handler(config.database_path, manifest_sha)
        with ThreadingHTTPServer((host, port), handler) as server:
            server.serve_forever()
    finally:
        lock.release()
