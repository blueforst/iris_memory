import json
from pathlib import Path

from iris_memory.cli import main


def test_cli_migrate_then_check(tmp_path: Path, capsys: object) -> None:
    data_root = tmp_path / "memory"

    assert main(["migrate", "--data-root", str(data_root)]) == 0
    migrate_output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert migrate_output["appliedVersions"] == ["0001_bootstrap"]

    assert main(["check", "--data-root", str(data_root)]) == 0
    check_output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert check_output["service"] == "iris-memory"
    assert check_output["status"] == "bootstrap"
    assert check_output["databaseExists"] is True
    assert check_output["capabilities"] == ["health.read"]
