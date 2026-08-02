"""Deterministic contract artifact building and verification tests."""

from __future__ import annotations

import json
from pathlib import Path

from iris_memory.contracts.artifact import (
    build_artifact_manifest,
    recompute_manifest_sha256,
    scan_fixtures,
    scan_openapi,
    scan_schemas,
    validate_fixtures,
    verify_artifact_directory,
    verify_manifest,
    write_contract_artifact,
)
from iris_memory.contracts.manifest import CONTRACT_PACKAGE


def test_manifest_builds_from_real_directory_scan() -> None:
    manifest = build_artifact_manifest()
    assert manifest["package"] == CONTRACT_PACKAGE.name
    assert manifest["version"] == CONTRACT_PACKAGE.version
    # Lists come from the REAL scanned directories, not a hand-written copy.
    assert manifest["schemas"] == scan_schemas()
    assert manifest["fixtures"] == scan_fixtures()
    assert manifest["openapi"] == scan_openapi()
    assert manifest["schemaCount"] == len(scan_schemas())
    assert manifest["fixtureCount"] == len(scan_fixtures())
    assert isinstance(manifest["manifestSha256"], str)
    assert len(str(manifest["manifestSha256"])) == 64


def test_manifest_checksum_is_deterministic_and_recomputable() -> None:
    first = build_artifact_manifest()
    second = build_artifact_manifest()
    assert first == second, "manifest build must be deterministic"
    recomputed = recompute_manifest_sha256(first)
    assert recomputed == first["manifestSha256"]


def test_verify_manifest_passes_for_built_manifest() -> None:
    manifest = build_artifact_manifest()
    ok, errors = verify_manifest(manifest)
    assert ok, errors


def test_verify_manifest_detects_checksum_tampering(tmp_path: Path) -> None:
    manifest = build_artifact_manifest()
    assert isinstance(manifest["checksums"], dict)
    checksums = manifest["checksums"]
    assert isinstance(checksums, dict) and isinstance(checksums.get("schemas"), dict)
    first_schema = next(iter(checksums["schemas"]))  # type: ignore[index]
    manifest["checksums"]["schemas"][first_schema] = "0" * 64  # type: ignore[index]
    ok, errors = verify_manifest(manifest)
    assert not ok
    assert any("checksum mismatch" in e for e in errors)


def test_verify_manifest_detects_manifest_sha_mismatch() -> None:
    manifest = build_artifact_manifest()
    manifest["manifestSha256"] = "0" * 64
    ok, errors = verify_manifest(manifest)
    assert not ok
    assert any("manifestSha256 mismatch" in e for e in errors)


def test_write_artifact_is_installable_and_verifiable(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    manifest_path = write_contract_artifact(artifact_dir)

    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["manifestSha256"] == recompute_manifest_sha256(manifest)

    # Every listed file physically exists in the unpacked artifact.
    for group in ("schemas", "fixtures", "openapi"):
        for relative in manifest[group]:
            assert (artifact_dir / relative).exists(), f"missing {relative}"

    # CI install/unpack verification: verify the on-disk directory.
    ok, errors = verify_artifact_directory(artifact_dir)
    assert ok, errors


def test_write_artifact_is_reproducible(tmp_path: Path) -> None:
    first_dir = tmp_path / "a"
    second_dir = tmp_path / "b"
    write_contract_artifact(first_dir)
    write_contract_artifact(second_dir)
    first = (first_dir / "manifest.json").read_bytes()
    second = (second_dir / "manifest.json").read_bytes()
    assert first == second, "artifact build must be byte-reproducible"


def test_validate_fixtures_all_pass() -> None:
    manifest = build_artifact_manifest()
    ok, errors = validate_fixtures(manifest)
    assert ok, errors


def test_scan_fixtures_has_valid_invalid_pairs() -> None:
    fixtures = scan_fixtures()
    assert len(fixtures) > 0
    for relative in fixtures:
        name = relative.rsplit("/", 1)[-1]
        assert ".valid." in name or ".invalid" in name
