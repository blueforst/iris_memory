import sqlite3
from pathlib import Path

from iris_memory.health import build_health_report


def test_health_reports_degraded_for_corrupt_database(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    database_path.write_bytes(b"not a sqlite database")

    report = build_health_report(database_path)

    assert report.status == "degraded"
    assert "publication.accept" not in report.capabilities


def test_health_reports_partial_ledger_as_degraded(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE accepted_publications (publication_id TEXT PRIMARY KEY)")

    report = build_health_report(database_path)

    assert report.status == "degraded"
    assert "publication.accept" not in report.capabilities
