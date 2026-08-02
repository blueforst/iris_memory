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

    contract = subcommands.add_parser("contract")
    contract_sub = contract.add_subparsers(dest="contract_command", required=True)
    build = contract_sub.add_parser("build")
    build.add_argument("--output-dir", type=Path, required=True)
    verify = contract_sub.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--dir", type=Path)

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
        from iris_memory.service import serve as serve_service

        serve_service(
            config,
            host=args.host,
            port=args.port,
        )
        return 0

    if args.command == "contract":
        from iris_memory.contracts.artifact import (
            build_artifact_manifest,
            verify_artifact_directory,
            verify_manifest,
            write_contract_artifact,
        )

        if args.contract_command == "build":
            manifest_path = write_contract_artifact(args.output_dir)
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "manifestPath": str(manifest_path),
                        "manifestSha256": build_artifact_manifest()["manifestSha256"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.contract_command == "verify":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            ok_manifest, manifest_errors = verify_manifest(manifest)
            if args.dir is not None:
                ok_dir, dir_errors = verify_artifact_directory(args.dir)
                errors = list(manifest_errors) + list(dir_errors)
                ok = ok_manifest and ok_dir
            else:
                errors = list(manifest_errors)
                ok = ok_manifest
            print(
                json.dumps(
                    {"status": "ok" if ok else "error", "errors": errors},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0 if ok else 1

    raise AssertionError(f"unhandled command: {args.command}")
