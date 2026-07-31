CREATE TABLE IF NOT EXISTS accepted_publications (
    publication_id TEXT PRIMARY KEY,
    contract_version TEXT NOT NULL,
    source_sequence INTEGER NOT NULL UNIQUE,
    canonical_payload_hash TEXT NOT NULL,
    receipt_id TEXT NOT NULL UNIQUE,
    accepted_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publication_idempotency (
    idempotency_key TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL UNIQUE,
    canonical_payload_hash TEXT NOT NULL,
    accepted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS acceptance_receipts (
    receipt_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'duplicate_replay')),
    receipt_json TEXT NOT NULL,
    accepted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_envelopes (
    envelope_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL UNIQUE,
    contract_version TEXT NOT NULL,
    source_sequence INTEGER NOT NULL,
    envelope_json TEXT NOT NULL,
    accepted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL UNIQUE,
    source_sequence INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'queued', 'running', 'succeeded', 'failed', 'quarantined')),
    graphiti_status TEXT NOT NULL DEFAULT 'not_configured',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_accepted_publications_sequence
    ON accepted_publications(source_sequence);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status
    ON ingestion_jobs(status);

INSERT OR IGNORE INTO service_metadata(key, value, updated_at)
VALUES ('router_state', 'ledger_initialized', CURRENT_TIMESTAMP);
