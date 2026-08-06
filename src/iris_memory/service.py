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

import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from iris_memory.acceptance import (
    SUPPORTED_MAJOR,
    SUPPORTED_MINORS,
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
    lifetime (M1 review fix).

    - The lock is a REAL kernel-held OS file lock (`fcntl.flock` on POSIX,
      `msvcrt.locking` on Windows), not an O_EXCL path-existence heuristic.
      The OS lock is authoritative: a second process blocks/fails against the
      kernel, and the lock is auto-released by the kernel if the holder dies.
    - PID/host/startedAt in the lockfile are DIAGNOSTIC ONLY and are never
      used to reap or judge a lock: stale cleanup without first acquiring the
      authoritative OS lock is forbidden (a live holder could be misjudged).
    - acquire() is blocking with a short timeout so a second service process
      fails fast; release() drops the kernel lock then removes the file.
    """

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fd: int | None = None

    @classmethod
    def acquire(
        cls,
        data_root: Path,
        *,
        timeout_ms: float = 3000,
    ) -> DataRootLock:
        lock_path = data_root / "memory.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            # Block up to timeout_ms for the kernel lock; fail fast after.
            cls._lock_fd(fd, timeout_ms)
            payload = json.dumps(
                {
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "startedAt": __import__("datetime").datetime.now().isoformat(),
                    "note": "diagnostic only; the OS lock is authoritative",
                }
            ).encode("utf-8")
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, payload)
            os.fsync(fd)
        except OSError as exc:
            os.close(fd)
            raise MemoryLockError(f"memory data root is locked: {lock_path}") from exc
        lock = cls(lock_path)
        lock._fd = fd
        return lock

    @staticmethod
    def _lock_fd(fd: int, timeout_ms: float) -> None:
        import time

        if os.name == "nt":
            import msvcrt

            locking = msvcrt.locking  # type: ignore[attr-defined]
            nb_lock = msvcrt.LK_NBLCK  # type: ignore[attr-defined]
            deadline = time.monotonic() + timeout_ms / 1000.0
            while True:
                try:
                    locking(fd, nb_lock, 1)
                    return
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)
        else:
            import fcntl

            # LOCK_EX | LOCK_NB, retry until timeout (fast-fail for a second
            # live process, kernel-held so a hard crash auto-releases).
            deadline = time.monotonic() + timeout_ms / 1000.0
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)

    def release(self) -> None:
        if self._fd is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                else:
                    import fcntl

                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(self._fd)
            self._fd = None
        # review-pass-2 #1: do NOT unlink. The lockfile inode is persistent;
        # unlinking would create a window where a successor creates a NEW
        # inode at the same path while another process still holds the OLD
        # inode's lock (double-hold on the same data root).


_MANIFEST_CACHE: dict[str, object] | None = None


def _cached_manifest() -> dict[str, object]:
    """Build the artifact manifest once per process (assets are immutable once
    installed); a per-request full directory scan is wasteful."""
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is None:
        _MANIFEST_CACHE = build_artifact_manifest()
    return _MANIFEST_CACHE


def build_capabilities() -> dict[str, object]:
    """Capability/version handshake payload (contract:
    capability-handshake-v2). Expresses exactly what is available and what is
    explicitly unavailable; Graphiti is not configured in this baseline."""
    manifest = _cached_manifest()
    readiness, degraded = _readiness()
    return {
        "schemaVersion": "capability-handshake-v2",
        "serviceVersion": CONTRACT_PACKAGE.version,
        "serviceName": "iris-memory",
        "contractPackage": CONTRACT_PACKAGE.name,
        "contractVersion": CONTRACT_PACKAGE.version,
        "contractVersions": {"major": SUPPORTED_MAJOR, "minor": max(SUPPORTED_MINORS)},
        "supportedMajor": SUPPORTED_MAJOR,
        "supportedMinor": max(SUPPORTED_MINORS),
        "supportedMinors": list(SUPPORTED_MINORS),
        "manifestSha256": manifest["manifestSha256"],
        "schemaCount": manifest["schemaCount"],
        "fixtureCount": manifest["fixtureCount"],
        "capabilities": list(_AVAILABLE_CAPABILITIES),
        "unavailableCapabilities": list(_UNAVAILABLE_CAPABILITIES),
        "graphitiStatus": "not_configured",
        "readiness": readiness,
        "degradedReasons": list(degraded),
    }


def _readiness() -> tuple[str, tuple[str, ...]]:
    """Graphiti is not configured, so the service is ready for durable
    Publication acceptance but must never claim a full Memory system ready.
    The health endpoint reports `degraded`; this handshake reports `ready`
    (for publication.accept) WITH the degraded reasons listed explicitly so
    clients can distinguish the two semantics."""
    degraded = (
        "graphiti_not_configured",
        "recall_not_implemented",
        "expand_not_implemented",
        "reindex_not_implemented",
        "stable_memory_ref_not_implemented",
    )
    return ("ready", degraded)


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
                        "schemaVersion": "not-implemented-error-v1",
                        "error": "not_implemented",
                        "code": "not_implemented",
                        "capability": "recall",
                        "message": "recall is not implemented in the R0 baseline",
                    },
                )
            elif path == "/v1/memory/expand" or path == "/memory/expand":
                self._send_json(
                    501,
                    {
                        "schemaVersion": "not-implemented-error-v1",
                        "error": "not_implemented",
                        "code": "not_implemented",
                        "capability": "expand",
                        "message": "expand is not implemented in the R0 baseline",
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
    """Acquire the lock, apply migrations, serve until SIGINT/SIGTERM, then
    perform a REAL graceful shutdown: stop accepting requests, wait for the
    server + worker threads, release the lock, and exit 0. A second process
    against the same data root fails fast (review-pass-2 #5)."""
    import signal as _signal
    import threading as _threading
    import time as _time

    config.ensure_directories()
    lock = DataRootLock.acquire(config.data_root)
    server: ThreadingHTTPServer | None = None
    stop_event = _threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        # Graceful: signal the accept loop to stop; the main thread then
        # closes the server (waits for in-flight handlers) before releasing
        # the lock.
        del signum, frame
        stop_event.set()

    prev_int = _signal.signal(_signal.SIGINT, _handle_signal)
    prev_term = _signal.signal(_signal.SIGTERM, _handle_signal)
    try:
        apply_migrations(config.database_path)
        manifest = _cached_manifest()
        manifest_sha = str(manifest["manifestSha256"])
        handler = make_handler(config.database_path, manifest_sha)
        server = ThreadingHTTPServer((host, port), handler)
        # Manual accept loop (serve_forever has no public stop-on-signal):
        # handle_request() blocks at most server.timeout, so a signal is
        # observed within the poll interval and the loop exits cleanly.
        server.timeout = 0.2
        while not stop_event.is_set():
            try:
                server.handle_request()
            except OSError:
                break

        # Stop accepting, wait for in-flight request threads, then release
        # resources in order (review-pass-2 #5).
        server.server_close()
        deadline = _time.monotonic() + 5.0
        while _time.monotonic() < deadline:
            active = [t for t in _threading.enumerate() if t is not _threading.main_thread()]
            workers = [
                t
                for t in active
                if t.name.startswith(("ThreadPoolExecutor", "Thread-", "Socketserver"))
            ]
            if not workers:
                break
            _time.sleep(0.1)
        server = None
    finally:
        _signal.signal(_signal.SIGINT, prev_int)
        _signal.signal(_signal.SIGTERM, prev_term)
        lock.release()


if __name__ == "__main__":
    raise RuntimeError("use `iris-memory serve` (CLI)")
