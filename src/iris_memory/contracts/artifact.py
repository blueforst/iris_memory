"""Deterministic contract artifact building and verification.

The JSON Schema set is the single authoritative wire contract. This module
scans the REAL packaged asset directories (never a second hand-written list),
computes SHA-256 checksums, and produces a versioned artifact whose manifest
checksum can be recomputed and verified by any consumer (iris-agent CI gate,
CI install/unpack, compatibility fail-closed).

Artifact layout (written by build_contract_artifact):

    manifest.json          complete manifest incl. per-file sha256 + manifestSha256
    schemas/*.schema.json
    fixtures/*.json
    openapi/*.json

Determinism: manifest JSON is emitted with sorted keys and compact
separators; the manifestSha256 covers the canonical bytes of the manifest
WITHOUT the self-referential manifestSha256 field, so verification is
stable and reproducible.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from iris_memory.contracts.assets import contract_asset
from iris_memory.contracts.manifest import CONTRACT_PACKAGE

SCHEMA_SUFFIX = ".schema.json"
FIXTURE_SUFFIX = ".json"
OPENAPI_DIR = "openapi"
SCHEMA_DIR = "schemas"
FIXTURE_DIR = "fixtures"


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    """One artifact file entry with its content-addressed checksum."""

    path: str
    sha256: str


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def scan_asset_files(relative_dir: str, suffix: str) -> list[str]:
    """Scan a packaged asset subdirectory for files ending in `suffix`.

    Returns sorted relative paths (e.g. "schemas/foo.schema.json"). The
    listing comes from the REAL directory — no second hand-written list.
    """
    from importlib.resources import as_file

    root = files("iris_memory.contracts.assets").joinpath(relative_dir)
    if not root.is_dir():
        return []
    with as_file(root) as root_path:
        return sorted(
            str(path.relative_to(root_path.parent))
            for path in root_path.iterdir()
            if path.is_file() and path.name.endswith(suffix)
        )


def scan_schemas() -> list[str]:
    return scan_asset_files(SCHEMA_DIR, SCHEMA_SUFFIX)


def scan_fixtures() -> list[str]:
    return scan_asset_files(FIXTURE_DIR, FIXTURE_SUFFIX)


def scan_openapi() -> list[str]:
    return scan_asset_files(OPENAPI_DIR, ".json")


def _asset_bytes(relative: str) -> bytes:
    with contract_asset(*relative.split("/")) as path:
        return path.read_bytes()


def build_artifact_manifest() -> dict[str, object]:
    """Build the complete artifact manifest from the REAL asset directories.

    The schemas/fixtures lists are produced by scanning the directories, so a
    file added to assets without a manifest update is still included, and a
    file removed is no longer listed — the manifest can never drift from the
    actual packaged assets.
    """
    schemas = [ArtifactFile(p, sha256_bytes(_asset_bytes(p))) for p in scan_schemas()]
    fixtures = [ArtifactFile(p, sha256_bytes(_asset_bytes(p))) for p in scan_fixtures()]
    openapi = [ArtifactFile(p, sha256_bytes(_asset_bytes(p))) for p in scan_openapi()]

    manifest_without_sha: dict[str, object] = {
        "package": CONTRACT_PACKAGE.name,
        "version": CONTRACT_PACKAGE.version,
        "majorVersion": CONTRACT_PACKAGE.major_version,
        "status": CONTRACT_PACKAGE.status,
        "authority": {
            "schemas": "authoritative",
            "openapi": "candidate_descriptive",
        },
        "schemaCount": len(schemas),
        "fixtureCount": len(fixtures),
        "schemas": [f.path for f in schemas],
        "fixtures": [f.path for f in fixtures],
        "openapi": [f.path for f in openapi],
        "checksums": {
            "schemas": {f.path: f.sha256 for f in schemas},
            "fixtures": {f.path: f.sha256 for f in fixtures},
            "openapi": {f.path: f.sha256 for f in openapi},
        },
    }
    manifest_sha = sha256_bytes(_canonical_bytes(manifest_without_sha))
    return {**manifest_without_sha, "manifestSha256": manifest_sha}


def recompute_manifest_sha256(manifest: dict[str, object]) -> str:
    """Recompute the manifest checksum, ignoring any stored manifestSha256."""
    without_self = {k: v for k, v in manifest.items() if k != "manifestSha256"}
    return sha256_bytes(_canonical_bytes(without_self))


def verify_manifest(manifest: dict[str, object]) -> tuple[bool, tuple[str, ...]]:
    """Verify a manifest against the REAL packaged assets.

    Checks that the schema/fixture lists match the scanned directories, that
    every listed file exists and has the recorded checksum, and that the
    manifestSha256 is exactly recomputable. Returns (valid, errors).
    """
    errors: list[str] = []

    declared_schemas = manifest.get("schemas")
    declared_fixtures = manifest.get("fixtures")
    declared_openapi = manifest.get("openapi")
    if not isinstance(declared_schemas, list) or not isinstance(declared_fixtures, list):
        errors.append("manifest is missing 'schemas'/'fixtures' lists")
        return (False, tuple(errors))

    actual_schemas = scan_schemas()
    actual_fixtures = scan_fixtures()
    actual_openapi = scan_openapi()

    declared_openapi_list: list[str] = (
        [str(e) for e in declared_openapi] if isinstance(declared_openapi, list) else []
    )
    declared_schema_list = [str(e) for e in declared_schemas]
    declared_fixture_list = [str(e) for e in declared_fixtures]
    if declared_schema_list != actual_schemas:
        errors.append(
            "schemas list drifts from scanned assets: "
            f"declared={declared_schemas} actual={actual_schemas}"
        )
    if declared_fixture_list != actual_fixtures:
        errors.append(
            "fixtures list drifts from scanned assets: "
            f"declared={declared_fixtures} actual={actual_fixtures}"
        )
    if declared_openapi_list != actual_openapi:
        errors.append(
            "openapi list drifts from scanned assets: "
            f"declared={declared_openapi} actual={actual_openapi}"
        )

    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict):
        errors.append("manifest is missing 'checksums'")
        return (False, tuple(errors))

    for group, paths in (
        ("schemas", actual_schemas),
        ("fixtures", actual_fixtures),
        ("openapi", actual_openapi),
    ):
        group_map = checksums.get(group)
        if not isinstance(group_map, dict):
            errors.append(f"checksums missing group '{group}'")
            continue
        for relative in paths:
            expected = group_map.get(relative)
            if not isinstance(expected, str):
                errors.append(f"checksum missing for {group}/{relative}")
                continue
            actual = sha256_bytes(_asset_bytes(relative))
            if actual != expected:
                errors.append(
                    f"checksum mismatch for {group}/{relative}: declared={expected} actual={actual}"
                )

    recomputed = recompute_manifest_sha256(manifest)
    declared_sha = manifest.get("manifestSha256")
    if declared_sha != recomputed:
        errors.append(f"manifestSha256 mismatch: declared={declared_sha} recomputed={recomputed}")

    return (not errors, tuple(errors))


def _schema_registry() -> object:
    """Registry of packaged schemas keyed by $id, so cross-file urn: $refs
    (v2 provenance schemas) resolve inside fixture validation."""
    from referencing import Registry, Resource  # type: ignore[import-not-found]

    resources: dict[str, Resource] = {}
    for relative in scan_schemas():
        with contract_asset(*relative.split("/")) as path:
            schema = json.loads(path.read_text(encoding="utf-8"))
        schema_id = str(schema.get("$id", ""))
        if schema_id:
            resources[schema_id] = Resource.from_contents(schema)
    return Registry(resources=resources)


def validate_fixtures(manifest: dict[str, object]) -> tuple[bool, tuple[str, ...]]:
    """Re-validate every valid/invalid fixture against its schema.

    Used by CI to prove the artifact's fixtures still agree with the
    authoritative schemas after packaging.
    """
    from jsonschema import Draft202012Validator, FormatChecker

    errors: list[str] = []
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list):
        return (False, ("manifest missing fixtures",))
    for relative in fixtures:
        name = relative.rsplit("/", 1)[-1]
        if ".valid." in name:
            schema_name = name.split(".valid.")[0]
            expect_valid = True
        elif ".invalid" in name:
            schema_name = name.split(".invalid")[0]
            expect_valid = False
        else:
            errors.append(f"fixture {relative} is neither *.valid.* nor *.invalid*")
            continue
        with contract_asset("schemas", f"{schema_name}.schema.json") as schema_path:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        with contract_asset("fixtures", name) as fixture_path:
            instance = json.loads(fixture_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(
            schema, format_checker=FormatChecker(), registry=_schema_registry()
        )
        validation_errors = list(validator.iter_errors(instance))
        if expect_valid and validation_errors:
            errors.append(f"{relative} should be valid but failed: {validation_errors}")
        if not expect_valid and not validation_errors:
            errors.append(f"{relative} should be invalid but validated cleanly")
    return (not errors, tuple(errors))


def write_contract_artifact(output_dir: Path) -> Path:
    """Write the full artifact (manifest + schemas + fixtures + openapi) into
    a fresh output directory and return the manifest path.

    M6 (review): the output directory must be EMPTY (or absent) so a reused
    directory cannot accumulate stale files that break the complete-artifact
    and byte-reproducibility guarantees.
    """
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(
            f"artifact output directory must be empty: {output_dir} (remove it or use a fresh path)"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_artifact_manifest()

    for group in (SCHEMA_DIR, FIXTURE_DIR, OPENAPI_DIR):
        (output_dir / group).mkdir(parents=True, exist_ok=True)

    for relative in [*scan_schemas(), *scan_fixtures(), *scan_openapi()]:
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_asset_bytes(relative))

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_bytes(_canonical_bytes(manifest) + b"\n")
    return manifest_path


def load_manifest(path: Path) -> dict[str, object]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def iter_manifest_files(manifest: dict[str, object]) -> Iterator[str]:
    for group in ("schemas", "fixtures", "openapi"):
        entries = manifest.get(group)
        if isinstance(entries, list):
            yield from (str(e) for e in entries)


def verify_artifact_directory(root: Path) -> tuple[bool, tuple[str, ...]]:
    """Verify an on-disk artifact directory (as CI unpacks it).

    Every file listed in the manifest must exist AND its bytes must hash to
    the manifest's recorded checksum — a tampered unpacked artifact is
    detected even when the manifest itself is intact.
    """
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        return (False, ("artifact is missing manifest.json",))
    manifest = load_manifest(manifest_path)
    errors: list[str] = []

    # M6 (review): verify the manifest STRUCTURE against its own declared
    # lists (the manifest is the self-contained authority — do not compare
    # against the installed package, which a consumer may not have).
    declared_schemas = manifest.get("schemas")
    declared_fixtures = manifest.get("fixtures")
    declared_openapi = manifest.get("openapi")
    if not isinstance(declared_schemas, list) or not isinstance(declared_fixtures, list):
        return (False, ("manifest is missing 'schemas'/'fixtures' lists",))
    manifest_sha = recompute_manifest_sha256(manifest)
    if manifest_sha != manifest.get("manifestSha256"):
        errors.append(
            f"manifestSha256 mismatch: declared={manifest.get('manifestSha256')} "
            f"recomputed={manifest_sha}"
        )

    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict):
        errors.append("manifest is missing 'checksums'")
        return (False, tuple(errors))

    declared_openapi_list = (
        [str(e) for e in declared_openapi] if isinstance(declared_openapi, list) else []
    )
    declared_all = [
        *(str(e) for e in declared_schemas),
        *(str(e) for e in declared_fixtures),
        *declared_openapi_list,
    ]
    expected_groups = {
        "schemas": [str(e) for e in declared_schemas],
        "fixtures": [str(e) for e in declared_fixtures],
        "openapi": declared_openapi_list,
    }

    # M6 / review-pass-2 #6 / review-pass-3 #2: the artifact is EXACTLY its
    # manifest surface — recursively. Everything under the root must be:
    #   - manifest.json (root file),
    #   - the three declared groups (schemas/fixtures/openapi), each
    #     containing ONLY the flat files the manifest lists — no nested
    #     directories, no extra files, no symlinks (flat layout is verified).
    extra: list[str] = []
    for path in root.iterdir():
        # review-pass-4 #2: a symlink is rejected REGARDLESS of its name —
        # even a root `manifest.json` symlink (pointing outside the artifact)
        # breaks the 'self-contained, no symlink' guarantee. Only a REGULAR
        # file named manifest.json is allowed.
        if path.is_symlink():
            extra.append(f"{path.name} (symlink)")
            continue
        if path.name == "manifest.json":
            continue
        if path.is_file():
            extra.append(path.name)
            continue
        if path.is_dir():
            if path.name not in ("schemas", "fixtures", "openapi"):
                extra.append(f"{path.name}/ (undeclared directory)")
                continue
            group = path.name
            for nested in path.rglob("*"):
                if nested.is_symlink():
                    extra.append(f"{group}/{nested.relative_to(path)} (symlink)")
                    continue
                if nested.is_dir():
                    extra.append(f"{group}/{nested.relative_to(path)}/ (nested directory)")
                    continue
                if nested.is_file():
                    relative = f"{group}/{nested.name}"
                    if relative not in declared_all:
                        extra.append(relative)
    if extra:
        errors.append(f"artifact directory contains entries not in the manifest: {sorted(extra)}")

    # M6: every declared file must exist AND hash to the recorded checksum.
    missing: list[str] = []
    content_mismatch: list[str] = []
    for group, relatives in expected_groups.items():
        group_map = checksums.get(group)
        if not isinstance(group_map, dict):
            errors.append(f"checksums missing group '{group}'")
            continue
        for relative in relatives:
            target = root / relative
            if not target.exists():
                missing.append(relative)
                continue
            recorded = group_map.get(relative)
            if not isinstance(recorded, str):
                errors.append(f"checksum missing for {relative}")
                continue
            actual = file_sha256(target)
            if actual != recorded:
                content_mismatch.append(f"{relative} (declared {recorded}, actual {actual})")
    if missing:
        errors.append(f"artifact directory missing files: {missing}")
    if content_mismatch:
        errors.append(f"artifact content checksum mismatch: {content_mismatch}")

    # M6: re-validate every fixture against the schema INSIDE the artifact
    # (self-contained — a consumer with only the artifact gets the same
    # verdict).
    fixture_errors: list[str] = []
    for relative in expected_groups["fixtures"]:
        name = relative.rsplit("/", 1)[-1]
        if ".valid." in name:
            schema_name = name.split(".valid.")[0]
            expect_valid = True
        elif ".invalid" in name:
            schema_name = name.split(".invalid")[0]
            expect_valid = False
        else:
            fixture_errors.append(f"fixture {relative} is neither *.valid.* nor *.invalid*")
            continue
        schema_path = root / "schemas" / f"{schema_name}.schema.json"
        fixture_path = root / relative
        if not schema_path.exists() or not fixture_path.exists():
            fixture_errors.append(f"fixture {relative} or its schema is missing")
            continue
        try:
            from jsonschema import Draft202012Validator, FormatChecker
            from referencing import Registry, Resource

            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            instance = json.loads(fixture_path.read_text(encoding="utf-8"))
            # Register every schema in this artifact directory by $id so v2
            # cross-file urn: refs resolve.
            resources: dict[str, Resource] = {}
            for schema_file in (root / "schemas").glob("*.schema.json"):
                s = json.loads(schema_file.read_text(encoding="utf-8"))
                schema_id = str(s.get("$id", ""))
                if schema_id:
                    resources[schema_id] = Resource.from_contents(s)
            registry = Registry(resources=resources)
            validator = Draft202012Validator(
                schema, format_checker=FormatChecker(), registry=registry
            )
            validation_errors = list(validator.iter_errors(instance))
        except Exception as exc:  # noqa: BLE001 - any failure is a verifier error
            fixture_errors.append(f"fixture {relative} validation failed: {exc}")
            continue
        if expect_valid and validation_errors:
            fixture_errors.append(f"{relative} should be valid but failed: {validation_errors}")
        if not expect_valid and not validation_errors:
            fixture_errors.append(f"{relative} should be invalid but validated cleanly")
    if fixture_errors:
        errors.append("fixture validation: " + "; ".join(fixture_errors))

    return (not errors, tuple(errors))
