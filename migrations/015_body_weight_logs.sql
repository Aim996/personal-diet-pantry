CREATE TABLE body_weight_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    measured_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', measured_at, '+0 seconds') = measured_at,
        0
    )),
    weight_g INTEGER NOT NULL CHECK (
        typeof(weight_g) = 'integer'
        AND weight_g >= 5000
        AND weight_g <= 500000
    ),
    status_note TEXT CHECK (
        status_note IS NULL
        OR (
            length(status_note) BETWEEN 1 AND 80
            AND status_note = trim(status_note)
        )
    ),
    version INTEGER NOT NULL DEFAULT 1 CHECK (
        typeof(version) = 'integer' AND version >= 1
    ),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')) CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')) CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', updated_at, '+0 seconds') = updated_at,
        0
    )),
    deleted_at TEXT CHECK (
        deleted_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', deleted_at, '+0 seconds') = deleted_at,
            0
        )
    ),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE INDEX idx_body_weight_logs_active_measured_at
ON body_weight_logs (measured_at DESC, id DESC)
WHERE deleted_at IS NULL;

ALTER TABLE operation_previews RENAME TO operation_previews_before_weight;

CREATE TABLE operation_previews (
    token_hash TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL CHECK (operation_type IN (
        'meal_preview',
        'water_preview',
        'pantry_add_preview',
        'pantry_deduct_preview',
        'pantry_adjust_preview',
        'water_reference',
        'weight_reference',
        'pantry_batch_reference',
        'meal_reference',
        'transaction_undo_reference',
        'transaction_redo_reference',
        'backup_reference'
    )),
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    resource_versions_json TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    expires_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', expires_at, '+0 seconds') = expires_at,
        0
    )),
    consumed_at TEXT CHECK (
        consumed_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', consumed_at, '+0 seconds') = consumed_at,
            0
        )
    ),
    transaction_id TEXT REFERENCES transactions(id) ON DELETE SET NULL,
    CHECK (julianday(expires_at) > julianday(created_at))
);

INSERT INTO operation_previews (
    token_hash,
    operation_type,
    request_json,
    result_json,
    resource_versions_json,
    created_at,
    expires_at,
    consumed_at,
    transaction_id
)
SELECT
    token_hash,
    operation_type,
    request_json,
    result_json,
    resource_versions_json,
    created_at,
    expires_at,
    consumed_at,
    transaction_id
FROM operation_previews_before_weight;

DROP TABLE operation_previews_before_weight;

CREATE INDEX idx_operation_previews_expiry
ON operation_previews (expires_at, consumed_at);

CREATE INDEX idx_operation_previews_type_expiry
ON operation_previews (operation_type, expires_at, consumed_at);
