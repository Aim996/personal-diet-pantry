ALTER TABLE pantry_batches ADD COLUMN total_weight_g REAL
    CHECK (
        total_weight_g IS NULL
        OR (
            typeof(total_weight_g) IN ('integer', 'real')
            AND total_weight_g > 0
        )
    );

ALTER TABLE pantry_batches ADD COLUMN average_unit_weight_g REAL
    CHECK (
        average_unit_weight_g IS NULL
        OR (
            typeof(average_unit_weight_g) IN ('integer', 'real')
            AND average_unit_weight_g > 0
        )
    );

ALTER TABLE pantry_batches ADD COLUMN weight_basis TEXT
    CHECK (
        weight_basis IS NULL
        OR weight_basis IN ('net', 'gross', 'shell_on', 'edible')
    );

ALTER TABLE pantry_batches ADD COLUMN weight_source TEXT;

ALTER TABLE pantry_batches ADD COLUMN weight_confidence TEXT
    CHECK (
        weight_confidence IS NULL
        OR weight_confidence IN ('confirmed', 'derived', 'estimated')
    );

CREATE TABLE nutrition_profiles (
    id INTEGER PRIMARY KEY,
    normalized_name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',
    product_key TEXT NOT NULL DEFAULT '',
    serving_basis TEXT NOT NULL CHECK (
        serving_basis IN ('per_100g', 'per_100ml', 'per_serving')
    ),
    nutrition_json TEXT NOT NULL CHECK (
        json_valid(nutrition_json) = 1
        AND substr(ltrim(nutrition_json), 1, 1) = '{'
    ),
    source_text TEXT NOT NULL,
    source_grade TEXT NOT NULL CHECK (
        source_grade IN ('A', 'B', 'C', 'D', 'unknown')
    ),
    profile_version INTEGER NOT NULL CHECK (
        typeof(profile_version) = 'integer'
        AND profile_version >= 1
    ),
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    UNIQUE (normalized_name, brand, product_key, profile_version)
);

CREATE TABLE pantry_nutrition_links (
    id INTEGER PRIMARY KEY,
    pantry_batch_id INTEGER NOT NULL UNIQUE
        REFERENCES pantry_batches(id) ON DELETE RESTRICT,
    nutrition_profile_id INTEGER NOT NULL
        REFERENCES nutrition_profiles(id) ON DELETE RESTRICT,
    nutrition_snapshot_json TEXT NOT NULL CHECK (
        json_valid(nutrition_snapshot_json) = 1
        AND substr(ltrim(nutrition_snapshot_json), 1, 1) = '{'
    ),
    linked_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', linked_at, '+0 seconds') = linked_at,
        0
    )),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE INDEX idx_nutrition_profiles_lookup
ON nutrition_profiles (
    normalized_name,
    brand,
    product_key,
    profile_version
);
