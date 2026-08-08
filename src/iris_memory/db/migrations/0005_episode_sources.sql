-- iris_memory#11: per-episode-source ingestion ledger (Graphiti-ready
-- boundary). Each accepted GraphitiEpisodeSource from a v3 publication gets
-- an immutable row carrying its deterministic memory-owned episode identity
-- (episode_id), the ordered position within the publication
-- (source_position), the canonical content hash, the target group, the
-- derivation/anti-echo flags and the ingestion attempt mapping
-- (graphiti_status / graphiti_episode_key / attempt_count / last_error).
-- The PK is (publication_id, episode_source_hash): the SAME episode content
-- (deterministic canonical hash) can legitimately appear in DIFFERENT
-- publications (replayed/rebuilt batches), and each publication keeps its
-- own immutable row + ingestion mapping. The publication-level ordered
-- ingestion job (ingestion_jobs) drives these rows in source_position
-- order. replay/restart is idempotent per (publication_id,
-- episode_source_hash).
CREATE TABLE IF NOT EXISTS accepted_episode_sources (
  episode_source_hash TEXT NOT NULL,
  publication_id TEXT NOT NULL REFERENCES accepted_publications(publication_id),
  source_position INTEGER NOT NULL,
  episode_id TEXT NOT NULL,
  lineage_id TEXT NOT NULL,
  from_context_seq INTEGER NOT NULL,
  to_context_seq INTEGER NOT NULL,
  target_group_id TEXT NOT NULL,
  canonical_content_hash TEXT NOT NULL,
  is_derived_only INTEGER NOT NULL DEFAULT 0,
  source_json TEXT NOT NULL,
  graphiti_status TEXT NOT NULL DEFAULT 'pending',
  graphiti_episode_key TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  ingested_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  PRIMARY KEY (publication_id, episode_source_hash),
  UNIQUE (publication_id, source_position)
);

CREATE INDEX IF NOT EXISTS idx_accepted_episode_sources_publication
  ON accepted_episode_sources(publication_id, source_position);
CREATE INDEX IF NOT EXISTS idx_accepted_episode_sources_source_hash
  ON accepted_episode_sources(episode_source_hash);
CREATE INDEX IF NOT EXISTS idx_accepted_episode_sources_status
  ON accepted_episode_sources(graphiti_status, created_at);
