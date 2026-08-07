CREATE TABLE transactions (
    id TEXT PRIMARY KEY,
    transaction_type TEXT NOT NULL CHECK (transaction_type IN (
        'meal_record', 'water_record', 'pantry_add', 'pantry_adjust', 'pantry_deduct',
        'meal_plan', 'record_correction', 'transaction_undo', 'transaction_redo',
        'report_query', 'profile_update', 'reminder_manage'
    )),
    status TEXT NOT NULL CHECK (status IN ('pending', 'committed', 'reverted', 'failed')),
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    committed_at TEXT CHECK (
        committed_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', committed_at, '+0 seconds') = committed_at,
            0
        )
    ),
    reverted_at TEXT CHECK (
        reverted_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', reverted_at, '+0 seconds') = reverted_at,
            0
        )
    ),
    source_text TEXT NOT NULL,
    before_snapshot TEXT,
    after_snapshot TEXT,
    error_message TEXT,
    CHECK (
        status NOT IN ('committed', 'reverted')
        OR (
            committed_at IS NOT NULL
            AND before_snapshot IS NOT NULL
            AND after_snapshot IS NOT NULL
            AND json_valid(before_snapshot) = 1
            AND json_valid(after_snapshot) = 1
            AND substr(ltrim(before_snapshot), 1, 1) = '['
            AND substr(ltrim(after_snapshot), 1, 1) = '['
        )
    ),
    CHECK (status <> 'reverted' OR reverted_at IS NOT NULL)
);

CREATE TABLE meals (
    id INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', occurred_at, '+0 seconds') = occurred_at,
        0
    )),
    meal_type TEXT NOT NULL CHECK (meal_type IN ('breakfast', 'lunch', 'dinner', 'snack', 'other')),
    source_text TEXT NOT NULL,
    location_type TEXT NOT NULL CHECK (location_type IN ('home', 'restaurant', 'takeout', 'unknown')),
    total_calories REAL CHECK (
        total_calories IS NULL
        OR (typeof(total_calories) IN ('integer', 'real') AND total_calories >= 0)
    ),
    total_protein REAL CHECK (
        total_protein IS NULL
        OR (typeof(total_protein) IN ('integer', 'real') AND total_protein >= 0)
    ),
    total_fat REAL CHECK (
        total_fat IS NULL OR (typeof(total_fat) IN ('integer', 'real') AND total_fat >= 0)
    ),
    total_carbohydrate REAL CHECK (
        total_carbohydrate IS NULL
        OR (typeof(total_carbohydrate) IN ('integer', 'real') AND total_carbohydrate >= 0)
    ),
    total_fiber REAL CHECK (
        total_fiber IS NULL OR (typeof(total_fiber) IN ('integer', 'real') AND total_fiber >= 0)
    ),
    total_sodium REAL CHECK (
        total_sodium IS NULL OR (typeof(total_sodium) IN ('integer', 'real') AND total_sodium >= 0)
    ),
    confidence REAL NOT NULL CHECK (
        typeof(confidence) IN ('integer', 'real') AND confidence >= 0 AND confidence <= 1
    ),
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
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE TABLE meal_items (
    id INTEGER PRIMARY KEY,
    meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    raw_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    amount REAL CHECK (
        amount IS NULL OR (typeof(amount) IN ('integer', 'real') AND amount >= 0)
    ),
    unit TEXT,
    consumed_weight_g REAL CHECK (
        consumed_weight_g IS NULL
        OR (typeof(consumed_weight_g) IN ('integer', 'real') AND consumed_weight_g >= 0)
    ),
    raw_weight_g REAL CHECK (
        raw_weight_g IS NULL
        OR (typeof(raw_weight_g) IN ('integer', 'real') AND raw_weight_g >= 0)
    ),
    inventory_deduction_weight_g REAL CHECK (
        inventory_deduction_weight_g IS NULL
        OR (
            typeof(inventory_deduction_weight_g) IN ('integer', 'real')
            AND inventory_deduction_weight_g >= 0
        )
    ),
    edible_ratio REAL CHECK (
        edible_ratio IS NULL
        OR (
            typeof(edible_ratio) IN ('integer', 'real')
            AND edible_ratio >= 0
            AND edible_ratio <= 1
        )
    ),
    cooking_yield REAL CHECK (
        cooking_yield IS NULL
        OR (typeof(cooking_yield) IN ('integer', 'real') AND cooking_yield >= 0)
    ),
    calories REAL CHECK (
        calories IS NULL OR (typeof(calories) IN ('integer', 'real') AND calories >= 0)
    ),
    protein REAL CHECK (
        protein IS NULL OR (typeof(protein) IN ('integer', 'real') AND protein >= 0)
    ),
    fat REAL CHECK (fat IS NULL OR (typeof(fat) IN ('integer', 'real') AND fat >= 0)),
    carbohydrate REAL CHECK (
        carbohydrate IS NULL
        OR (typeof(carbohydrate) IN ('integer', 'real') AND carbohydrate >= 0)
    ),
    fiber REAL CHECK (fiber IS NULL OR (typeof(fiber) IN ('integer', 'real') AND fiber >= 0)),
    sodium REAL CHECK (
        sodium IS NULL OR (typeof(sodium) IN ('integer', 'real') AND sodium >= 0)
    ),
    source_grade TEXT NOT NULL CHECK (source_grade IN ('A', 'B', 'C', 'D', 'unknown')),
    confidence REAL NOT NULL CHECK (
        typeof(confidence) IN ('integer', 'real') AND confidence >= 0 AND confidence <= 1
    ),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE TABLE water_logs (
    id INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', occurred_at, '+0 seconds') = occurred_at,
        0
    )),
    amount_ml INTEGER NOT NULL CHECK (
        typeof(amount_ml) IN ('integer', 'real') AND amount_ml > 0
    ),
    source_text TEXT NOT NULL,
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

CREATE TABLE pantry_batches (
    id INTEGER PRIMARY KEY,
    food_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    batch_code TEXT,
    storage_location TEXT,
    purchase_date TEXT,
    added_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', added_at, '+0 seconds') = added_at,
        0
    )),
    opened_at TEXT CHECK (
        opened_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', opened_at, '+0 seconds') = opened_at,
            0
        )
    ),
    expires_at TEXT CHECK (
        expires_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', expires_at, '+0 seconds') = expires_at,
            0
        )
    ),
    initial_quantity REAL NOT NULL CHECK (
        typeof(initial_quantity) IN ('integer', 'real') AND initial_quantity >= 0
    ),
    remaining_quantity REAL NOT NULL CHECK (
        typeof(remaining_quantity) IN ('integer', 'real') AND remaining_quantity >= 0
    ),
    unit TEXT NOT NULL,
    price REAL CHECK (
        price IS NULL OR (typeof(price) IN ('integer', 'real') AND price >= 0)
    ),
    status TEXT NOT NULL CHECK (status IN (
        'active', 'opened', 'frozen', 'thawed', 'discarded', 'expired', 'consumed'
    )),
    source TEXT NOT NULL,
    notes TEXT,
    version INTEGER NOT NULL CHECK (typeof(version) = 'integer' AND version >= 1),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE TABLE pantry_movements (
    id INTEGER PRIMARY KEY,
    pantry_batch_id INTEGER NOT NULL REFERENCES pantry_batches(id) ON DELETE RESTRICT,
    movement_type TEXT NOT NULL CHECK (movement_type IN (
        'add', 'consume', 'adjust', 'discard', 'expire', 'restore', 'open', 'freeze', 'thaw'
    )),
    quantity REAL NOT NULL CHECK (
        typeof(quantity) IN ('integer', 'real') AND quantity >= 0
    ),
    unit TEXT NOT NULL,
    reason TEXT,
    linked_meal_id INTEGER REFERENCES meals(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE TABLE nutrition_cache (
    id INTEGER PRIMARY KEY,
    normalized_name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',
    serving_basis TEXT NOT NULL,
    nutrition_json TEXT NOT NULL,
    source TEXT NOT NULL,
    source_grade TEXT NOT NULL CHECK (source_grade IN ('A', 'B', 'C', 'D', 'unknown')),
    verified_at TEXT CHECK (
        verified_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', verified_at, '+0 seconds') = verified_at,
            0
        )
    ),
    expires_at TEXT CHECK (
        expires_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', expires_at, '+0 seconds') = expires_at,
            0
        )
    ),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    UNIQUE (normalized_name, brand, serving_basis)
);

CREATE TABLE personal_rules (
    id INTEGER PRIMARY KEY,
    rule_type TEXT NOT NULL CHECK (
        rule_type IN ('food_alias', 'portion', 'meal_type', 'inventory_link', 'preference')
    ),
    subject TEXT NOT NULL,
    rule_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (
        typeof(confidence) IN ('integer', 'real') AND confidence >= 0 AND confidence <= 1
    ),
    evidence_count INTEGER NOT NULL CHECK (
        typeof(evidence_count) = 'integer' AND evidence_count >= 0
    ),
    source TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (typeof(active) = 'integer' AND active IN (0, 1)),
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    updated_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', updated_at, '+0 seconds') = updated_at,
        0
    )),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE TABLE learning_events (
    id INTEGER PRIMARY KEY,
    rule_id INTEGER NOT NULL REFERENCES personal_rules(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('observed', 'confirmed', 'rejected', 'promoted', 'demoted')
    ),
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE TABLE pending_inventory_links (
    id INTEGER PRIMARY KEY,
    meal_item_id INTEGER NOT NULL REFERENCES meal_items(id) ON DELETE CASCADE,
    candidate_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (
        typeof(confidence) IN ('integer', 'real') AND confidence >= 0 AND confidence <= 1
    ),
    status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'rejected', 'expired')),
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    resolved_at TEXT CHECK (
        resolved_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', resolved_at, '+0 seconds') = resolved_at,
            0
        )
    ),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE TABLE operation_previews (
    token_hash TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL CHECK (operation_type IN (
        'meal_preview', 'water_preview', 'pantry_deduct_preview', 'pantry_adjust_preview'
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

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', applied_at, '+0 seconds') = applied_at,
        0
    )),
    checksum TEXT NOT NULL
);
