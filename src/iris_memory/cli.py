"""Bootstrap CLI for migration and readiness inspection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = MemoryServiceConfig.from_data_root(
        args.data_root,
        database_path=args.database_path,
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

    raise AssertionError(f"unhandled command: {args.command}")
