"""Minimal stdlib HTTP surface for the acceptance vertical slice."""

from __future__ import annotations

import json
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
from iris_memory.db import apply_migrations
from iris_memory.health import build_health_report


def make_handler(database_path: Path) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to one SQLite database path."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/health":
                self._send_json(200, build_health_report(database_path).as_dict())
            else:
                self._send_json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            path = self.path.rstrip("/")
            if path == "/historian/publications":
                self._handle_publication()
            elif path == "/memory/recall":
                self._send_json(
                    501,
                    {
                        "schemaVersion": "recall-request-v1",
                        "error": "recall_not_implemented",
                    },
                )
            elif path == "/memory/expand":
                self._send_json(
                    501,
                    {
                        "schemaVersion": "expansion-request-v1",
                        "error": "expand_not_implemented",
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
    """Apply migrations and serve until interrupted."""
    config.ensure_directories()
    apply_migrations(config.database_path)
    handler = make_handler(config.database_path)
    with ThreadingHTTPServer((host, port), handler) as server:
        server.serve_forever()
