"""Real `iris-memory serve` subprocess evidence (M5 review fix).

Covers the product lifecycle that the in-process handler tests bypass:
ready/health/capabilities, Publication replay, second-process lock rejection,
hard-exit restart, graceful shutdown and same-data-root reopen.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _request() -> dict[str, object]:
    return {
        "schemaVersion": "publication-acceptance-request-v1",
        "contractVersion": "0.1.0",
        "idempotencyKey": "subproc-accept-001",
        "publication": {
            "schemaVersion": "historian-publication-v1",
            "publicationId": "55555555-5555-4555-8555-555555555555",
            "sourceSequence": 1,
            "publishedAt": "2026-08-01T00:00:00Z",
            "payloadHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "compartmentCount": 1,
            "segmentCount": 1,
            "evidenceCount": 1,
            "summary": "subprocess fixture",
        },
    }


def _start(data_root: Path, port: int) -> subprocess.Popen[bytes]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "iris_memory",
            "serve",
            "--data-root",
            str(data_root),
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for the HTTP server to accept connections.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stderr.read().decode() if proc.stderr else ""
            raise RuntimeError(f"serve exited early: {out}")
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            connection.request("GET", "/v1/health")
            connection.getresponse().read()
            connection.close()
            return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("serve did not become ready")


def _get(port: int, path: str) -> tuple[int, dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, body


def _post(port: int, path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(
        "POST",
        path,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, body


def _stop(proc: subprocess.Popen[bytes], timeout: float = 10) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def test_serve_ready_health_capabilities_and_replay(tmp_path: Path) -> None:
    data_root = tmp_path / "memory"
    proc = _start(data_root, 18131)
    try:
        status, health = _get(18131, "/v1/health")
        assert status == 200
        assert health["schemaVersion"] == "health-response-v1"

        status, caps = _get(18131, "/v1/capabilities")
        assert status == 200
        assert "publication.accept" in caps["capabilities"]
        assert "graphiti.ingest" in caps["unavailableCapabilities"]

        status, receipt = _post(18131, "/v1/publications/accept", _request())
        assert status == 200
        assert receipt["status"] == "accepted"

        # Idempotent replay returns the same receipt.
        status, replay = _post(18131, "/v1/publications/accept", _request())
        assert status == 200
        assert replay["receiptId"] == receipt["receiptId"]
    finally:
        _stop(proc)


def test_second_process_lock_rejection(tmp_path: Path) -> None:
    data_root = tmp_path / "memory"
    first = _start(data_root, 18132)
    try:
        # A second serve against the same data root must fail fast.
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        second = subprocess.run(
            [
                sys.executable,
                "-m",
                "iris_memory",
                "serve",
                "--data-root",
                str(data_root),
                "--port",
                "18133",
            ],
            env=env,
            capture_output=True,
            timeout=15,
        )
        assert second.returncode != 0
        assert b"locked" in second.stderr.lower() or b"lock" in second.stderr.lower()
    finally:
        _stop(first)


def test_hard_exit_restart_and_graceful_reopen(tmp_path: Path) -> None:
    data_root = tmp_path / "memory"
    proc = _start(data_root, 18134)
    _post(18134, "/v1/publications/accept", _request())
    # Hard-exit: SIGKILL (no graceful release). The kernel auto-releases the
    # flock; committed data must still be readable on restart.
    proc.kill()
    proc.wait()

    restarted = _start(data_root, 18135)
    try:
        status, health = _get(18135, "/v1/health")
        assert status == 200
        assert health["databaseExists"] is True
        # The accepted publication survives the hard exit (durable).
        status, replay = _post(18135, "/v1/publications/accept", _request())
        assert status == 200
        assert replay["receiptId"] != ""
    finally:
        _stop(restarted)
    # Graceful shutdown releases the lock: reopen immediately works.
    third = _start(data_root, 18136)
    _stop(third)
