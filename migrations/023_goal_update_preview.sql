ALTER TABLE operation_previews
RENAME TO operation_previews_before_goal_update_preview;

CREATE TABLE operation_previews (
    token_hash TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL CHECK (operation_type IN (
        'meal_preview', 'water_preview', 'pantry_add_preview',
        'pantry_deduct_preview', 'pantry_adjust_preview', 'water_reference',
        'weight_reference', 'pantry_batch_reference', 'pantry_product_reference',
        'prepared_food_reference', 'meal_reference',
        'transaction_undo_reference', 'transaction_redo_reference',
        'backup_reference', 'shopping_list_preview',
        'shopping_list_reference', 'import_preview', 'delete_data_preview',
        'export_reference', 'restore_preview', 'goal_update_preview'
    )),
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    resource_versions_json TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at, 0
    )),
    expires_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', expires_at, '+0 seconds') = expires_at, 0
    )),
    consumed_at TEXT CHECK (
        consumed_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', consumed_at, '+0 seconds') = consumed_at, 0
        )
    ),
    transaction_id TEXT REFERENCES transactions(id) ON DELETE SET NULL,
    CHECK (julianday(expires_at) > julianday(created_at))
);

INSERT INTO operation_previews (
    token_hash, operation_type, request_json, result_json,
    resource_versions_json, created_at, expires_at, consumed_at, transaction_id
)
SELECT token_hash, operation_type, request_json, result_json,
       resource_versions_json, created_at, expires_at, consumed_at, transaction_id
FROM operation_previews_before_goal_update_preview;

DROP TABLE operation_previews_before_goal_update_preview;

CREATE INDEX idx_operation_previews_expiry
ON operation_previews(expires_at, consumed_at);

CREATE INDEX idx_operation_previews_type_expiry
ON operation_previews(operation_type, expires_at, consumed_at);
