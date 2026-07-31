"""Health report with explicit bootstrap/degraded semantics."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from iris_memory.contracts.manifest import CONTRACT_PACKAGE

HealthStatus = Literal["bootstrap", "not_ready", "ready", "degraded"]
_REQUIRED_LEDGER_TABLES = frozenset(
    {
        "accepted_publications",
        "publication_idempotency",
        "acceptance_receipts",
        "evidence_envelopes",
        "ingestion_jobs",
    }
)


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Machine-readable health state for the memory service."""

    service: str
    status: HealthStatus
    contract_version: str
    database_exists: bool
    graphiti_status: Literal["not_configured"]
    capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": "health-response-v1",
            "service": self.service,
            "status": self.status,
            "contractVersion": self.contract_version,
            "databaseExists": self.database_exists,
            "graphitiStatus": self.graphiti_status,
            "capabilities": list(self.capabilities),
        }


def _ledger_state(
    database_path: Path,
) -> Literal["missing", "bootstrap", "partial", "corrupt", "initialized"]:
    if not database_path.exists():
        return "missing"
    try:
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            state = connection.execute(
                "SELECT value FROM service_metadata WHERE key = 'router_state'"
            ).fetchone()
    except sqlite3.Error:
        return "corrupt"

    if tables >= _REQUIRED_LEDGER_TABLES and state == ("ledger_initialized",):
        return "initialized"
    if tables & _REQUIRED_LEDGER_TABLES:
        return "partial"
    return "bootstrap"


def build_health_report(database_path: Path) -> HealthReport:
    """Return an honest report; Graphiti is not configured in this slice."""
    state = _ledger_state(database_path)
    if state == "initialized":
        return HealthReport(
            service="iris-memory",
            status="degraded",
            contract_version=CONTRACT_PACKAGE.version,
            database_exists=True,
            graphiti_status="not_configured",
            capabilities=("health.read", "publication.accept"),
        )
    if state in {"partial", "corrupt"}:
        return HealthReport(
            service="iris-memory",
            status="degraded",
            contract_version=CONTRACT_PACKAGE.version,
            database_exists=True,
            graphiti_status="not_configured",
            capabilities=("health.read",),
        )
    return HealthReport(
        service="iris-memory",
        status="bootstrap",
        contract_version=CONTRACT_PACKAGE.version,
        database_exists=state != "missing",
        graphiti_status="not_configured",
        capabilities=("health.read",),
    )
