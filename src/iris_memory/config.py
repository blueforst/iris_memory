"""Bootstrap configuration without committing to a web framework."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MemoryServiceConfig:
    """Filesystem configuration owned by the memory service process."""

    data_root: Path
    database_path: Path

    @classmethod
    def from_data_root(
        cls,
        data_root: Path,
        *,
        database_path: Path | None = None,
    ) -> "MemoryServiceConfig":
        resolved_root = data_root.expanduser().resolve()
        resolved_database = (
            database_path.expanduser().resolve()
            if database_path is not None
            else resolved_root / "router.sqlite3"
        )
        return cls(data_root=resolved_root, database_path=resolved_database)

    def ensure_directories(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
