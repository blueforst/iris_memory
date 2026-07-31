import json

from jsonschema import Draft202012Validator

from iris_memory.contracts import CONTRACT_PACKAGE, contract_asset


def test_capability_handshake_fixture_matches_schema() -> None:
    with contract_asset("schemas", "capability-handshake-v1.schema.json") as schema_path:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    with contract_asset("fixtures", "capability-handshake-v1.valid.json") as fixture_path:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(fixture)
    assert fixture["contractVersion"] == CONTRACT_PACKAGE.version
    assert fixture["capabilities"] == ["health.read"]
