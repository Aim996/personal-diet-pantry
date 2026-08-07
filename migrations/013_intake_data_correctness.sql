ALTER TABLE meals ADD COLUMN event_timezone TEXT
CHECK (event_timezone IS NULL OR length(trim(event_timezone)) > 0);

ALTER TABLE meals ADD COLUMN local_date TEXT
CHECK (
    local_date IS NULL
    OR (
        length(local_date) = 10
        AND date(local_date) = local_date
    )
);

ALTER TABLE meals ADD COLUMN intake_fingerprint TEXT
CHECK (
    intake_fingerprint IS NULL
    OR (
        length(intake_fingerprint) = 64
        AND intake_fingerprint NOT GLOB '*[^0-9a-f]*'
    )
);

ALTER TABLE meals ADD COLUMN source_session_hash TEXT
CHECK (
    source_session_hash IS NULL
    OR (
        length(source_session_hash) = 64
        AND source_session_hash NOT GLOB '*[^0-9a-f]*'
    )
);

ALTER TABLE meals ADD COLUMN nutrition_calculation_status TEXT NOT NULL
DEFAULT 'unverified'
CHECK (
    nutrition_calculation_status
    IN ('valid', 'invalid', 'unverified')
);

ALTER TABLE meals ADD COLUMN nutrition_provenance_status TEXT NOT NULL
DEFAULT 'untraceable'
CHECK (
    nutrition_provenance_status
    IN ('traceable', 'partial', 'untraceable')
);

ALTER TABLE meal_items ADD COLUMN consumed_volume_ml TEXT
CHECK (
    consumed_volume_ml IS NULL
    OR (
        typeof(consumed_volume_ml) = 'text'
        AND consumed_volume_ml <> ''
        AND consumed_volume_ml NOT GLOB '*[^0-9.]*'
        AND consumed_volume_ml NOT GLOB '*.*.*'
        AND consumed_volume_ml NOT LIKE '.%'
        AND consumed_volume_ml NOT LIKE '%.'
        AND CAST(consumed_volume_ml AS NUMERIC) >= 0
    )
);

ALTER TABLE meal_items ADD COLUMN consumed_servings TEXT
CHECK (
    consumed_servings IS NULL
    OR (
        typeof(consumed_servings) = 'text'
        AND consumed_servings <> ''
        AND consumed_servings NOT GLOB '*[^0-9.]*'
        AND consumed_servings NOT GLOB '*.*.*'
        AND consumed_servings NOT LIKE '.%'
        AND consumed_servings NOT LIKE '%.'
        AND CAST(consumed_servings AS NUMERIC) >= 0
    )
);

CREATE TABLE meal_item_nutrition_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_item_id INTEGER NOT NULL UNIQUE
        REFERENCES meal_items(id) ON DELETE CASCADE,
    basis TEXT NOT NULL
        CHECK (
            basis IN (
                'per_100g',
                'per_100ml',
                'per_serving',
                'consumed_total'
            )
        ),
    input_facts_json TEXT NOT NULL
        CHECK (
            json_valid(input_facts_json) = 1
            AND json_type(input_facts_json) = 'object'
        ),
    scale_factor TEXT NOT NULL
        CHECK (
            typeof(scale_factor) = 'text'
            AND scale_factor <> ''
            AND scale_factor NOT GLOB '*[^0-9.]*'
            AND scale_factor NOT GLOB '*.*.*'
            AND scale_factor NOT LIKE '.%'
            AND scale_factor NOT LIKE '%.'
            AND CAST(scale_factor AS NUMERIC) >= 0
        ),
    source_name TEXT NOT NULL,
    source_grade TEXT NOT NULL
        CHECK (source_grade IN ('A', 'B', 'C', 'D')),
    dataset_version TEXT,
    rules_version TEXT NOT NULL,
    portion_evidence_json TEXT NOT NULL DEFAULT '{}'
        CHECK (
            json_valid(portion_evidence_json) = 1
            AND json_type(portion_evidence_json) = 'object'
        ),
    calculation_status TEXT NOT NULL
        CHECK (calculation_status IN ('valid', 'invalid')),
    provenance_status TEXT NOT NULL
        CHECK (
            provenance_status IN ('traceable', 'partial', 'untraceable')
        ),
    warnings_json TEXT NOT NULL DEFAULT '[]'
        CHECK (
            json_valid(warnings_json) = 1
            AND json_type(warnings_json) = 'array'
    ),
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    transaction_id TEXT NOT NULL REFERENCES transactions(id)
);

CREATE UNIQUE INDEX idx_meals_active_intake_fingerprint
ON meals (intake_fingerprint)
WHERE deleted_at IS NULL AND intake_fingerprint IS NOT NULL;

CREATE INDEX idx_meals_local_date
ON meals (local_date, deleted_at);
