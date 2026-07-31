"""Repository ownership boundary for Iris Memory."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectBoundary:
    """Stable repository-level ownership declaration."""

    project: str
    owns: tuple[str, ...]
    excludes: tuple[str, ...]


PROJECT_BOUNDARY = ProjectBoundary(
    project="iris-memory",
    owns=(
        "publication-acceptance",
        "evidence-ledger",
        "assessment-ledger",
        "ordered-ingestion",
        "memory-router",
        "stable-memory-ref",
        "recall-disposition",
        "graphiti-adapter",
        "recall-expand-api",
        "reindex",
        "memory-contracts",
    ),
    excludes=(
        "pi-session",
        "runtime-session-epoch",
        "agent-context",
        "historian-database",
        "persona",
        "tool-loop",
        "body-runtime",
    ),
)
