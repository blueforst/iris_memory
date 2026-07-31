import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from iris_memory.acceptance_http import make_handler
from iris_memory.db import apply_migrations


def _start_server(database_path: Path) -> tuple[ThreadingHTTPServer, threading.Thread]:
    apply_migrations(database_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(database_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request() -> dict[str, Any]:
    return {
        "schemaVersion": "publication-acceptance-request-v1",
        "contractVersion": "0.1.0",
        "idempotencyKey": "http-accept-001",
        "publication": {
            "schemaVersion": "historian-publication-v1",
            "publicationId": "33333333-3333-4333-8333-333333333333",
            "sourceSequence": 1,
            "publishedAt": "2026-08-01T00:00:00Z",
            "payloadHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "compartmentCount": 1,
            "segmentCount": 1,
            "evidenceCount": 1,
            "summary": "http acceptance fixture",
        },
    }


def test_http_health_and_publication_acceptance(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    server, thread = _start_server(database_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)

        connection.request("GET", "/health")
        response = connection.getresponse()
        assert response.status == 200
        health = json.loads(response.read().decode("utf-8"))
        assert health["status"] == "degraded"

        body = json.dumps(_request())
        connection.request(
            "POST",
            "/historian/publications",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        first = json.loads(response.read().decode("utf-8"))
        assert first["status"] == "accepted"

        connection.request(
            "POST",
            "/historian/publications",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        replay = json.loads(response.read().decode("utf-8"))
        assert replay["receiptId"] == first["receiptId"]

        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
