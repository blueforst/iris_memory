-- 0004: introduce migration checksum bookkeeping as a forward migration.
--
-- Adds the `checksum` column to schema_migrations if it does not already
-- exist (pre-round-3 databases get the column via this forward migration,
-- not via runtime ALTER). The authoritative checksum VALUES for previously
-- applied migrations are provided by the release-owned checksums manifest
-- (db/migrations/checksums.json) — the runner never derives trust for
-- historical rows from whatever bytes are on disk today.

ALTER TABLE schema_migrations ADD COLUMN checksum TEXT NOT NULL DEFAULT '';
