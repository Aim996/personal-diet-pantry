ALTER TABLE meals ADD COLUMN nutrition_status TEXT NOT NULL DEFAULT 'incomplete'
CHECK (nutrition_status IN ('complete', 'partial', 'incomplete'));

ALTER TABLE meals ADD COLUMN nutrition_missing_fields_json TEXT NOT NULL DEFAULT '[]'
CHECK (
    json_valid(nutrition_missing_fields_json) = 1
    AND substr(ltrim(nutrition_missing_fields_json), 1, 1) = '['
);

ALTER TABLE meals ADD COLUMN source_session_key TEXT;
ALTER TABLE meals ADD COLUMN source_model TEXT;
ALTER TABLE meals ADD COLUMN test_run_id TEXT;

ALTER TABLE water_logs ADD COLUMN source_session_key TEXT;
ALTER TABLE water_logs ADD COLUMN source_model TEXT;
ALTER TABLE water_logs ADD COLUMN test_run_id TEXT;

CREATE TABLE nutrition_goal_profiles (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    calories_kcal INTEGER NOT NULL CHECK (calories_kcal > 0),
    protein_g INTEGER NOT NULL CHECK (protein_g > 0),
    fat_g INTEGER NOT NULL CHECK (fat_g > 0),
    carbohydrate_g INTEGER NOT NULL CHECK (carbohydrate_g > 0),
    fiber_g INTEGER NOT NULL CHECK (fiber_g > 0),
    sodium_mg INTEGER NOT NULL CHECK (sodium_mg > 0),
    water_ml INTEGER NOT NULL CHECK (water_ml > 0),
    timezone_name TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    transaction_id TEXT REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE TABLE semantic_operation_receipts (
    semantic_fingerprint TEXT PRIMARY KEY CHECK (
        length(semantic_fingerprint) = 64
        AND semantic_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    transaction_id TEXT NOT NULL UNIQUE
        REFERENCES transactions(id) ON DELETE RESTRICT,
    committed_at TEXT NOT NULL
);

UPDATE meals
SET
    nutrition_status = CASE
        WHEN total_calories IS NOT NULL
         AND total_protein IS NOT NULL
         AND total_fat IS NOT NULL
         AND total_carbohydrate IS NOT NULL
         AND total_fiber IS NOT NULL
         AND total_sodium IS NOT NULL THEN 'complete'
        WHEN total_calories IS NULL
         AND total_protein IS NULL
         AND total_fat IS NULL
         AND total_carbohydrate IS NULL
         AND total_fiber IS NULL
         AND total_sodium IS NULL THEN 'incomplete'
        ELSE 'partial'
    END,
    nutrition_missing_fields_json = json_array(
        CASE WHEN total_calories IS NULL THEN 'calories' END,
        CASE WHEN total_protein IS NULL THEN 'protein' END,
        CASE WHEN total_fat IS NULL THEN 'fat' END,
        CASE WHEN total_carbohydrate IS NULL THEN 'carbohydrate' END,
        CASE WHEN total_fiber IS NULL THEN 'fiber' END,
        CASE WHEN total_sodium IS NULL THEN 'sodium' END
    );

UPDATE meals
SET nutrition_missing_fields_json = '[]'
WHERE nutrition_status = 'complete';

UPDATE meals
SET nutrition_missing_fields_json = (
    SELECT json_group_array(field)
    FROM (
        SELECT 'calories' AS field, 1 AS position WHERE total_calories IS NULL
        UNION ALL SELECT 'protein', 2 WHERE total_protein IS NULL
        UNION ALL SELECT 'fat', 3 WHERE total_fat IS NULL
        UNION ALL SELECT 'carbohydrate', 4 WHERE total_carbohydrate IS NULL
        UNION ALL SELECT 'fiber', 5 WHERE total_fiber IS NULL
        UNION ALL SELECT 'sodium', 6 WHERE total_sodium IS NULL
        ORDER BY position
    )
)
WHERE nutrition_status <> 'complete';
