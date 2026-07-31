"""JSON Schema validation helpers for packaged contract assets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker

from iris_memory.contracts.assets import contract_asset


def load_schema(schema_name: str) -> dict[str, object]:
    """Load a packaged schema as a plain dictionary."""
    with contract_asset("schemas", schema_name) as path:
        return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def validate_instance(schema_name: str, instance: object) -> tuple[bool, tuple[str, ...]]:
    """Validate an instance; return (valid, error messages)."""
    schema = load_schema(schema_name)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = tuple(
        f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in validator.iter_errors(instance)
    )
    return (not errors, errors)


def read_json(path: Path) -> dict[str, object]:
    """Read a JSON asset without narrowing its shape."""
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
