"""CLI for migration, acceptance and local HTTP serving."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from iris_memory.acceptance import (
    AcceptanceOutcome,
    Accepted,
    DuplicateReplay,
    IdempotencyConflict,
    SequenceConflict,
    UnsupportedVersion,
    ValidationFailure,
    accept_publication,
)
from iris_memory.acceptance_http import serve
from iris_memory.config import MemoryServiceConfig
from iris_memory.db import apply_migrations
from iris_memory.health import build_health_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iris-memory")
    subcommands = parser.add_subparsers(dest="command", required=True)

    for name in ("migrate", "check"):
        command = subcommands.add_parser(name)
        command.add_argument("--data-root", type=Path, required=True)
        command.add_argument("--database-path", type=Path)

    accept = subcommands.add_parser("accept")
    accept.add_argument("--data-root", type=Path, required=True)
    accept.add_argument("--database-path", type=Path)
    accept.add_argument("--request-file", type=Path, required=True)

    serve_command = subcommands.add_parser("serve")
    serve_command.add_argument("--data-root", type=Path, required=True)
    serve_command.add_argument("--database-path", type=Path)
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=18011)

    return parser


def _outcome_as_dict(outcome: AcceptanceOutcome) -> dict[str, object]:
    if isinstance(outcome, ValidationFailure):
        return {"status": outcome.status, "errors": list(outcome.errors)}
    if isinstance(outcome, (Accepted, DuplicateReplay)):
        return {"status": outcome.status, "receipt": outcome.receipt}
    if isinstance(outcome, (IdempotencyConflict, SequenceConflict, UnsupportedVersion)):
        return {"status": outcome.status, "error": outcome.error}
    raise AssertionError(f"unhandled outcome: {outcome!r}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = MemoryServiceConfig.from_data_root(
        args.data_root,
        database_path=getattr(args, "database_path", None),
    )

    if args.command == "migrate":
        config.ensure_directories()
        result = apply_migrations(config.database_path)
        print(
            json.dumps(
                {
                    "databasePath": str(result.database_path),
                    "appliedVersions": result.applied_versions,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "check":
        report = build_health_report(config.database_path)
        print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "accept":
        request = json.loads(args.request_file.read_text(encoding="utf-8"))
        outcome = accept_publication(config.database_path, request)
        print(json.dumps(_outcome_as_dict(outcome), ensure_ascii=False, sort_keys=True))
        return 0 if isinstance(outcome, (Accepted, DuplicateReplay)) else 1

    if args.command == "serve":
        serve(
            config,
            host=args.host,
            port=args.port,
        )
        return 0

    raise AssertionError(f"unhandled command: {args.command}")
