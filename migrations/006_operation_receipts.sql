CREATE TABLE operation_receipts (
    operation_id TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL CHECK (
        length(request_fingerprint) = 64
        AND request_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    transaction_id TEXT NOT NULL UNIQUE
        REFERENCES transactions(id) ON DELETE RESTRICT,
    committed_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', committed_at, '+0 seconds') = committed_at,
        0
    ))
);

CREATE INDEX idx_operation_receipts_transaction
ON operation_receipts (transaction_id);
