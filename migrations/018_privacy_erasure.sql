CREATE TABLE portable_entity_handles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_kind TEXT NOT NULL CHECK (
        length(trim(entity_kind)) BETWEEN 1 AND 64
    ),
    entity_key TEXT NOT NULL CHECK (
        length(trim(entity_key)) BETWEEN 1 AND 128
    ),
    external_handle TEXT NOT NULL UNIQUE CHECK (
        length(external_handle) BETWEEN 24 AND 160
    ),
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    erased_at TEXT CHECK (
        erased_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', erased_at, '+0 seconds') = erased_at,
            0
        )
    ),
    UNIQUE (entity_kind, entity_key)
);

CREATE INDEX idx_portable_entity_handles_active
ON portable_entity_handles (entity_kind, entity_key)
WHERE erased_at IS NULL;

CREATE TABLE privacy_erasure_tombstones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    erasure_handle TEXT NOT NULL UNIQUE CHECK (
        length(erasure_handle) BETWEEN 24 AND 160
    ),
    preview_token_hash TEXT NOT NULL UNIQUE CHECK (
        length(preview_token_hash) = 64
        AND preview_token_hash NOT GLOB '*[^0-9a-f]*'
    ),
    scope TEXT NOT NULL CHECK (
        scope IN (
            'raw_source_text',
            'preferences',
            'intake_range',
            'business_facts_keep_config',
            'all_business'
        )
    ),
    affected_counts_json TEXT NOT NULL CHECK (
        json_valid(affected_counts_json) = 1
        AND json_type(affected_counts_json) = 'object'
    ),
    summary_sha256 TEXT NOT NULL CHECK (
        length(summary_sha256) = 64
        AND summary_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    committed_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', committed_at, '+0 seconds') = committed_at,
        0
    ))
);

CREATE INDEX idx_privacy_erasure_tombstones_committed
ON privacy_erasure_tombstones (committed_at DESC, id DESC);

ALTER TABLE operation_previews
RENAME TO operation_previews_before_privacy_erasure;

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
        'backup_reference',
        'shopping_list_preview',
        'shopping_list_reference',
        'import_preview',
        'delete_data_preview',
        'export_reference'
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
FROM operation_previews_before_privacy_erasure;

DROP TABLE operation_previews_before_privacy_erasure;

CREATE INDEX idx_operation_previews_expiry
ON operation_previews (expires_at, consumed_at);

CREATE INDEX idx_operation_previews_type_expiry
ON operation_previews (operation_type, expires_at, consumed_at);
