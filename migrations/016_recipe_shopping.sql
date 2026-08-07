CREATE TABLE recipe_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 120),
    normalized_name TEXT NOT NULL UNIQUE CHECK (
        length(trim(normalized_name)) BETWEEN 1 AND 120
    ),
    ingredients_json TEXT NOT NULL CHECK (
        json_valid(ingredients_json) = 1
        AND json_type(ingredients_json) = 'array'
        AND json_array_length(ingredients_json) BETWEEN 1 AND 30
    ),
    yield_quantity REAL NOT NULL CHECK (
        typeof(yield_quantity) IN ('integer', 'real') AND yield_quantity > 0
    ),
    yield_unit TEXT NOT NULL CHECK (length(trim(yield_unit)) BETWEEN 1 AND 24),
    notes TEXT CHECK (notes IS NULL OR length(notes) BETWEEN 1 AND 500),
    source_text TEXT NOT NULL CHECK (length(trim(source_text)) BETWEEN 1 AND 1000),
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    updated_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', updated_at, '+0 seconds') = updated_at,
        0
    )),
    deleted_at TEXT CHECK (
        deleted_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', deleted_at, '+0 seconds') = deleted_at,
            0
        )
    ),
    version INTEGER NOT NULL DEFAULT 1 CHECK (
        typeof(version) = 'integer' AND version >= 1
    ),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE INDEX idx_recipe_profiles_active_updated
ON recipe_profiles (updated_at DESC, id DESC)
WHERE deleted_at IS NULL;

CREATE TABLE shopping_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 120),
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled', 'completed')),
    source_text TEXT NOT NULL CHECK (length(trim(source_text)) BETWEEN 1 AND 1000),
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    updated_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', updated_at, '+0 seconds') = updated_at,
        0
    )),
    cancelled_at TEXT CHECK (
        cancelled_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', cancelled_at, '+0 seconds') = cancelled_at,
            0
        )
    ),
    version INTEGER NOT NULL DEFAULT 1 CHECK (
        typeof(version) = 'integer' AND version >= 1
    ),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE INDEX idx_shopping_lists_status_updated
ON shopping_lists (status, updated_at DESC, id DESC);

CREATE TABLE shopping_list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shopping_list_id INTEGER NOT NULL REFERENCES shopping_lists(id) ON DELETE RESTRICT,
    food_name TEXT NOT NULL CHECK (length(trim(food_name)) BETWEEN 1 AND 120),
    normalized_name TEXT NOT NULL CHECK (
        length(trim(normalized_name)) BETWEEN 1 AND 120
    ),
    quantity REAL NOT NULL CHECK (
        typeof(quantity) IN ('integer', 'real') AND quantity > 0
    ),
    unit TEXT NOT NULL CHECK (length(trim(unit)) BETWEEN 1 AND 24),
    reason TEXT CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 240),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'purchased', 'cancelled')
    ),
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    updated_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', updated_at, '+0 seconds') = updated_at,
        0
    )),
    version INTEGER NOT NULL DEFAULT 1 CHECK (
        typeof(version) = 'integer' AND version >= 1
    ),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE INDEX idx_shopping_list_items_list_order
ON shopping_list_items (shopping_list_id, id);

ALTER TABLE operation_previews RENAME TO operation_previews_before_recipe_shopping;

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
        'shopping_list_reference'
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
FROM operation_previews_before_recipe_shopping;

DROP TABLE operation_previews_before_recipe_shopping;

CREATE INDEX idx_operation_previews_expiry
ON operation_previews (expires_at, consumed_at);

CREATE INDEX idx_operation_previews_type_expiry
ON operation_previews (operation_type, expires_at, consumed_at);
