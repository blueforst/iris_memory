-- 0003: rebuild publication_idempotency without the publication_id UNIQUE
-- constraint so a single accepted publication can bind multiple consumed
-- idempotency keys (alternate-key replay and conflict consumption).

CREATE TABLE publication_idempotency_rebuild (
    idempotency_key TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL,
    canonical_payload_hash TEXT NOT NULL,
    accepted_at TEXT NOT NULL
);

INSERT INTO publication_idempotency_rebuild
    (idempotency_key, publication_id, canonical_payload_hash, accepted_at)
    SELECT idempotency_key, publication_id, canonical_payload_hash, accepted_at
    FROM publication_idempotency;

DROP TABLE publication_idempotency;

ALTER TABLE publication_idempotency_rebuild RENAME TO publication_idempotency;

CREATE INDEX IF NOT EXISTS idx_publication_idempotency_publication
    ON publication_idempotency(publication_id);
