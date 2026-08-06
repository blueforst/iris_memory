"""JSON Schema validation helpers for packaged contract assets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource  # type: ignore[import-not-found]

from iris_memory.contracts.assets import contract_asset

_registry: Registry | None = None


def _schema_registry() -> Registry:
    """Registry of every packaged schema, so cross-file `$ref` (urn:) resolves.

    v2 schemas reference reusable types (evidence-basis-ref, context-range,
    raw-archive-ref, semantic-derivation-refs) by urn id; a bare
    Draft202012Validator cannot resolve them. All packaged schemas are
    registered by their $id, matching the manifest's authoritative set.
    """
    global _registry
    if _registry is None:
        from iris_memory.contracts.artifact import scan_schemas

        resources: dict[str, Resource] = {}
        for relative in scan_schemas():
            with contract_asset(*relative.split("/")) as path:
                schema = json.loads(path.read_text(encoding="utf-8"))
            schema_id = str(schema.get("$id", ""))
            if schema_id:
                resources[schema_id] = Resource.from_contents(schema)
        _registry = Registry(resources=resources)
    return _registry


def load_schema(schema_name: str) -> dict[str, object]:
    """Load a packaged schema as a plain dictionary."""
    with contract_asset("schemas", schema_name) as path:
        return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def validate_instance(schema_name: str, instance: object) -> tuple[bool, tuple[str, ...]]:
    """Validate an instance; return (valid, error messages)."""
    schema = load_schema(schema_name)
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
        registry=_schema_registry(),
    )
    errors = tuple(
        f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in validator.iter_errors(instance)
    )
    return (not errors, errors)


def read_json(path: Path) -> dict[str, object]:
    """Read a JSON asset without narrowing its shape."""
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
