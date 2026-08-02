import json
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from iris_memory.contracts import CONTRACT_PACKAGE, contract_asset


def _manifest() -> dict[str, Any]:
    with contract_asset("manifest.json") as path:
        return json.loads(path.read_text(encoding="utf-8"))


def _read(relative: str) -> Any:
    with contract_asset(*relative.split("/")) as path:
        return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_matches_contract_package() -> None:
    manifest = _manifest()
    assert manifest["package"] == CONTRACT_PACKAGE.name
    assert manifest["version"] == CONTRACT_PACKAGE.version
    assert manifest["package"] == "iris-memory-contracts"


def test_capability_handshake_matches_contract_package() -> None:
    fixture = _read("fixtures/capability-handshake-v1.valid.json")
    assert fixture["contractVersion"] == CONTRACT_PACKAGE.version


def test_all_manifest_schemas_are_valid_draft2020() -> None:
    for relative in _manifest()["schemas"]:
        schema = _read(relative)
        Draft202012Validator.check_schema(schema)


def test_manifest_fixtures_validate_or_fail_as_expected() -> None:
    for relative in _manifest()["fixtures"]:
        name = relative.rsplit("/", 1)[-1]
        assert ".valid." in name or ".invalid" in name
        schema_name = name.split(".valid.")[0] if ".valid." in name else name.split(".invalid")[0]
        schema = _read(f"schemas/{schema_name}.schema.json")
        instance = _read(relative)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = list(validator.iter_errors(instance))
        if ".valid." in name:
            assert not errors, f"{relative} should be valid: {errors}"
        else:
            assert errors, f"{relative} should be invalid"


def test_openapi_asset_exists_and_parses() -> None:
    with contract_asset("openapi", "iris-memory-v1.json") as path:
        document = json.loads(path.read_text(encoding="utf-8"))
    assert document["openapi"].startswith("3.")
    assert set(document["paths"]) == {
        "/health",
        "/historian/publications",
        "/memory/recall",
        "/memory/expand",
        "/v1/capabilities",
        "/v1/memory/recall",
        "/v1/memory/expand",
    }


def test_manifest_declares_json_schema_as_authoritative() -> None:
    manifest = _manifest()
    assert manifest["authority"]["schemas"] == "authoritative"
    assert manifest["authority"]["openapi"] == "candidate_descriptive"
    assert manifest["openapi"][0]["status"] == "candidate"
    assert manifest["openapi"][0]["authority"] == "descriptive"
