ALTER TABLE pantry_batches ADD COLUMN initial_display_quantity REAL
    CHECK (
        initial_display_quantity IS NULL
        OR initial_display_quantity > 0
    );

ALTER TABLE pantry_batches ADD COLUMN display_unit TEXT
    CHECK (
        display_unit IS NULL
        OR length(trim(display_unit)) BETWEEN 1 AND 40
    );

ALTER TABLE pantry_batches ADD COLUMN base_quantity_per_display_unit REAL
    CHECK (
        base_quantity_per_display_unit IS NULL
        OR base_quantity_per_display_unit > 0
    );

ALTER TABLE pantry_batches ADD COLUMN package_hierarchy_json TEXT
    CHECK (
        package_hierarchy_json IS NULL
        OR (
            json_valid(package_hierarchy_json) = 1
            AND substr(ltrim(package_hierarchy_json), 1, 1) = '['
        )
    );

CREATE INDEX idx_pantry_batches_product_package
ON pantry_batches (
    normalized_name,
    unit,
    display_unit,
    status,
    expires_at,
    added_at
);

ALTER TABLE operation_previews
RENAME TO operation_previews_before_prepared_food_reference;

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
        'export_reference', 'restore_preview'
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
FROM operation_previews_before_prepared_food_reference;

DROP TABLE operation_previews_before_prepared_food_reference;

CREATE INDEX idx_operation_previews_expiry
ON operation_previews(expires_at, consumed_at);

CREATE INDEX idx_operation_previews_type_expiry
ON operation_previews(operation_type, expires_at, consumed_at);
