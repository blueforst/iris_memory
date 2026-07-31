"""Memory Router persistence bootstrap."""

from iris_memory.db.migrate import MigrationResult, apply_migrations

__all__ = ["MigrationResult", "apply_migrations"]
