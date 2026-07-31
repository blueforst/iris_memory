CREATE TABLE IF NOT EXISTS service_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO service_metadata(key, value, updated_at)
VALUES ('repository_state', 'bootstrap', CURRENT_TIMESTAMP);
