"""Bootstrap health report with explicit not-ready semantics."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from iris_memory.contracts.manifest import CONTRACT_PACKAGE


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Machine-readable status for the repository bootstrap."""

    service: str
    status: Literal["bootstrap", "not_ready", "ready", "degraded"]
    contract_version: str
    database_exists: bool
    graphiti_status: Literal["not_configured"]
    capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "service": self.service,
            "status": self.status,
            "contractVersion": self.contract_version,
            "databaseExists": self.database_exists,
            "graphitiStatus": self.graphiti_status,
            "capabilities": list(self.capabilities),
        }


def build_health_report(database_path: Path) -> HealthReport:
    """Return an honest bootstrap report without probing external services."""

    return HealthReport(
        service="iris-memory",
        status="bootstrap",
        contract_version=CONTRACT_PACKAGE.version,
        database_exists=database_path.exists(),
        graphiti_status="not_configured",
        capabilities=("health.read",),
    )
