ALTER TABLE pantry_batches
ADD COLUMN source_meal_id INTEGER
REFERENCES meals(id) ON DELETE RESTRICT;

CREATE TABLE prepared_food_profiles (
    id INTEGER PRIMARY KEY,
    pantry_batch_id INTEGER NOT NULL UNIQUE
        REFERENCES pantry_batches(id) ON DELETE RESTRICT,
    source_meal_id INTEGER NOT NULL
        REFERENCES meals(id) ON DELETE RESTRICT,
    nutrition_basis TEXT NOT NULL
        CHECK (nutrition_basis = 'portion_total'),
    nutrition_json TEXT NOT NULL CHECK (
        json_valid(nutrition_json) = 1
        AND substr(ltrim(nutrition_json), 1, 1) = '{'
    ),
    initial_quantity REAL NOT NULL CHECK (
        typeof(initial_quantity) IN ('integer', 'real')
        AND initial_quantity > 0
    ),
    unit TEXT NOT NULL,
    source_grade TEXT NOT NULL CHECK (
        source_grade IN ('A', 'B', 'C', 'D', 'unknown')
    ),
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    transaction_id TEXT NOT NULL
        REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE INDEX idx_prepared_food_profiles_source_meal
ON prepared_food_profiles (source_meal_id);
