CREATE TEMP TABLE migration_003_meals AS
SELECT * FROM meals;

CREATE TEMP TABLE migration_003_meal_items AS
SELECT * FROM meal_items;

CREATE TEMP TABLE migration_003_pending_inventory_links AS
SELECT * FROM pending_inventory_links;

CREATE TEMP TABLE migration_003_pantry_meal_links AS
SELECT id, linked_meal_id
FROM pantry_movements
WHERE linked_meal_id IS NOT NULL;

DROP TABLE meal_items;
DROP TABLE meals;

CREATE TABLE meals (
    id INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', occurred_at, '+0 seconds') = occurred_at,
        0
    )),
    meal_type TEXT NOT NULL CHECK (meal_type IN ('breakfast', 'lunch', 'dinner', 'snack', 'other')),
    source_text TEXT NOT NULL,
    location_type TEXT NOT NULL CHECK (location_type IN ('home', 'restaurant', 'takeout', 'unknown')),
    total_calories TEXT CHECK (
        total_calories IS NULL
        OR (
            typeof(total_calories) = 'text'
            AND total_calories <> ''
            AND total_calories NOT GLOB '*[^0-9.]*'
            AND total_calories NOT GLOB '*.*.*'
            AND total_calories NOT LIKE '.%'
            AND total_calories NOT LIKE '%.'
            AND CAST(total_calories AS NUMERIC) >= 0
        )
    ),
    total_protein TEXT CHECK (
        total_protein IS NULL
        OR (
            typeof(total_protein) = 'text'
            AND total_protein <> ''
            AND total_protein NOT GLOB '*[^0-9.]*'
            AND total_protein NOT GLOB '*.*.*'
            AND total_protein NOT LIKE '.%'
            AND total_protein NOT LIKE '%.'
            AND CAST(total_protein AS NUMERIC) >= 0
        )
    ),
    total_fat TEXT CHECK (
        total_fat IS NULL
        OR (
            typeof(total_fat) = 'text'
            AND total_fat <> ''
            AND total_fat NOT GLOB '*[^0-9.]*'
            AND total_fat NOT GLOB '*.*.*'
            AND total_fat NOT LIKE '.%'
            AND total_fat NOT LIKE '%.'
            AND CAST(total_fat AS NUMERIC) >= 0
        )
    ),
    total_carbohydrate TEXT CHECK (
        total_carbohydrate IS NULL
        OR (
            typeof(total_carbohydrate) = 'text'
            AND total_carbohydrate <> ''
            AND total_carbohydrate NOT GLOB '*[^0-9.]*'
            AND total_carbohydrate NOT GLOB '*.*.*'
            AND total_carbohydrate NOT LIKE '.%'
            AND total_carbohydrate NOT LIKE '%.'
            AND CAST(total_carbohydrate AS NUMERIC) >= 0
        )
    ),
    total_fiber TEXT CHECK (
        total_fiber IS NULL
        OR (
            typeof(total_fiber) = 'text'
            AND total_fiber <> ''
            AND total_fiber NOT GLOB '*[^0-9.]*'
            AND total_fiber NOT GLOB '*.*.*'
            AND total_fiber NOT LIKE '.%'
            AND total_fiber NOT LIKE '%.'
            AND CAST(total_fiber AS NUMERIC) >= 0
        )
    ),
    total_sodium TEXT CHECK (
        total_sodium IS NULL
        OR (
            typeof(total_sodium) = 'text'
            AND total_sodium <> ''
            AND total_sodium NOT GLOB '*[^0-9.]*'
            AND total_sodium NOT GLOB '*.*.*'
            AND total_sodium NOT LIKE '.%'
            AND total_sodium NOT LIKE '%.'
            AND CAST(total_sodium AS NUMERIC) >= 0
        )
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

INSERT INTO meals (
    id, occurred_at, meal_type, source_text, location_type,
    total_calories, total_protein, total_fat, total_carbohydrate, total_fiber, total_sodium,
    confidence, created_at, updated_at, deleted_at, transaction_id
)
SELECT
    id, occurred_at, meal_type, source_text, location_type,
    CASE WHEN total_calories IS NULL THEN NULL
         WHEN instr(lower(CAST(total_calories AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', total_calories), '0'), '.')
         WHEN instr(CAST(total_calories AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(total_calories AS TEXT), '0'), '.')
         ELSE CAST(total_calories AS TEXT) END,
    CASE WHEN total_protein IS NULL THEN NULL
         WHEN instr(lower(CAST(total_protein AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', total_protein), '0'), '.')
         WHEN instr(CAST(total_protein AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(total_protein AS TEXT), '0'), '.')
         ELSE CAST(total_protein AS TEXT) END,
    CASE WHEN total_fat IS NULL THEN NULL
         WHEN instr(lower(CAST(total_fat AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', total_fat), '0'), '.')
         WHEN instr(CAST(total_fat AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(total_fat AS TEXT), '0'), '.')
         ELSE CAST(total_fat AS TEXT) END,
    CASE WHEN total_carbohydrate IS NULL THEN NULL
         WHEN instr(lower(CAST(total_carbohydrate AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', total_carbohydrate), '0'), '.')
         WHEN instr(CAST(total_carbohydrate AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(total_carbohydrate AS TEXT), '0'), '.')
         ELSE CAST(total_carbohydrate AS TEXT) END,
    CASE WHEN total_fiber IS NULL THEN NULL
         WHEN instr(lower(CAST(total_fiber AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', total_fiber), '0'), '.')
         WHEN instr(CAST(total_fiber AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(total_fiber AS TEXT), '0'), '.')
         ELSE CAST(total_fiber AS TEXT) END,
    CASE WHEN total_sodium IS NULL THEN NULL
         WHEN instr(lower(CAST(total_sodium AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', total_sodium), '0'), '.')
         WHEN instr(CAST(total_sodium AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(total_sodium AS TEXT), '0'), '.')
         ELSE CAST(total_sodium AS TEXT) END,
    confidence, created_at, updated_at, deleted_at, transaction_id
FROM migration_003_meals;

CREATE TABLE meal_items (
    id INTEGER PRIMARY KEY,
    meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    raw_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    amount TEXT CHECK (
        amount IS NULL
        OR (
            typeof(amount) = 'text'
            AND amount <> ''
            AND amount NOT GLOB '*[^0-9.]*'
            AND amount NOT GLOB '*.*.*'
            AND amount NOT LIKE '.%'
            AND amount NOT LIKE '%.'
            AND CAST(amount AS NUMERIC) >= 0
        )
    ),
    unit TEXT,
    consumed_weight_g TEXT CHECK (
        consumed_weight_g IS NULL
        OR (
            typeof(consumed_weight_g) = 'text'
            AND consumed_weight_g <> ''
            AND consumed_weight_g NOT GLOB '*[^0-9.]*'
            AND consumed_weight_g NOT GLOB '*.*.*'
            AND consumed_weight_g NOT LIKE '.%'
            AND consumed_weight_g NOT LIKE '%.'
            AND CAST(consumed_weight_g AS NUMERIC) >= 0
        )
    ),
    raw_weight_g TEXT CHECK (
        raw_weight_g IS NULL
        OR (
            typeof(raw_weight_g) = 'text'
            AND raw_weight_g <> ''
            AND raw_weight_g NOT GLOB '*[^0-9.]*'
            AND raw_weight_g NOT GLOB '*.*.*'
            AND raw_weight_g NOT LIKE '.%'
            AND raw_weight_g NOT LIKE '%.'
            AND CAST(raw_weight_g AS NUMERIC) >= 0
        )
    ),
    inventory_deduction_weight_g TEXT CHECK (
        inventory_deduction_weight_g IS NULL
        OR (
            typeof(inventory_deduction_weight_g) = 'text'
            AND inventory_deduction_weight_g <> ''
            AND inventory_deduction_weight_g NOT GLOB '*[^0-9.]*'
            AND inventory_deduction_weight_g NOT GLOB '*.*.*'
            AND inventory_deduction_weight_g NOT LIKE '.%'
            AND inventory_deduction_weight_g NOT LIKE '%.'
            AND CAST(inventory_deduction_weight_g AS NUMERIC) >= 0
        )
    ),
    edible_ratio TEXT CHECK (
        edible_ratio IS NULL
        OR (
            typeof(edible_ratio) = 'text'
            AND edible_ratio <> ''
            AND edible_ratio NOT GLOB '*[^0-9.]*'
            AND edible_ratio NOT GLOB '*.*.*'
            AND edible_ratio NOT LIKE '.%'
            AND edible_ratio NOT LIKE '%.'
            AND CAST(edible_ratio AS NUMERIC) >= 0
            AND CAST(edible_ratio AS NUMERIC) <= 1
        )
    ),
    cooking_yield TEXT CHECK (
        cooking_yield IS NULL
        OR (
            typeof(cooking_yield) = 'text'
            AND cooking_yield <> ''
            AND cooking_yield NOT GLOB '*[^0-9.]*'
            AND cooking_yield NOT GLOB '*.*.*'
            AND cooking_yield NOT LIKE '.%'
            AND cooking_yield NOT LIKE '%.'
            AND CAST(cooking_yield AS NUMERIC) >= 0
        )
    ),
    calories TEXT CHECK (
        calories IS NULL
        OR (
            typeof(calories) = 'text'
            AND calories <> ''
            AND calories NOT GLOB '*[^0-9.]*'
            AND calories NOT GLOB '*.*.*'
            AND calories NOT LIKE '.%'
            AND calories NOT LIKE '%.'
            AND CAST(calories AS NUMERIC) >= 0
        )
    ),
    protein TEXT CHECK (
        protein IS NULL
        OR (
            typeof(protein) = 'text'
            AND protein <> ''
            AND protein NOT GLOB '*[^0-9.]*'
            AND protein NOT GLOB '*.*.*'
            AND protein NOT LIKE '.%'
            AND protein NOT LIKE '%.'
            AND CAST(protein AS NUMERIC) >= 0
        )
    ),
    fat TEXT CHECK (
        fat IS NULL
        OR (
            typeof(fat) = 'text'
            AND fat <> ''
            AND fat NOT GLOB '*[^0-9.]*'
            AND fat NOT GLOB '*.*.*'
            AND fat NOT LIKE '.%'
            AND fat NOT LIKE '%.'
            AND CAST(fat AS NUMERIC) >= 0
        )
    ),
    carbohydrate TEXT CHECK (
        carbohydrate IS NULL
        OR (
            typeof(carbohydrate) = 'text'
            AND carbohydrate <> ''
            AND carbohydrate NOT GLOB '*[^0-9.]*'
            AND carbohydrate NOT GLOB '*.*.*'
            AND carbohydrate NOT LIKE '.%'
            AND carbohydrate NOT LIKE '%.'
            AND CAST(carbohydrate AS NUMERIC) >= 0
        )
    ),
    fiber TEXT CHECK (
        fiber IS NULL
        OR (
            typeof(fiber) = 'text'
            AND fiber <> ''
            AND fiber NOT GLOB '*[^0-9.]*'
            AND fiber NOT GLOB '*.*.*'
            AND fiber NOT LIKE '.%'
            AND fiber NOT LIKE '%.'
            AND CAST(fiber AS NUMERIC) >= 0
        )
    ),
    sodium TEXT CHECK (
        sodium IS NULL
        OR (
            typeof(sodium) = 'text'
            AND sodium <> ''
            AND sodium NOT GLOB '*[^0-9.]*'
            AND sodium NOT GLOB '*.*.*'
            AND sodium NOT LIKE '.%'
            AND sodium NOT LIKE '%.'
            AND CAST(sodium AS NUMERIC) >= 0
        )
    ),
    source_grade TEXT NOT NULL CHECK (source_grade IN ('A', 'B', 'C', 'D', 'unknown')),
    confidence REAL NOT NULL CHECK (
        typeof(confidence) IN ('integer', 'real') AND confidence >= 0 AND confidence <= 1
    ),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

INSERT INTO meal_items (
    id, meal_id, raw_name, normalized_name, amount, unit,
    consumed_weight_g, raw_weight_g, inventory_deduction_weight_g,
    edible_ratio, cooking_yield, calories, protein, fat, carbohydrate, fiber, sodium,
    source_grade, confidence, transaction_id
)
SELECT
    id, meal_id, raw_name, normalized_name,
    CASE WHEN amount IS NULL THEN NULL
         WHEN instr(lower(CAST(amount AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', amount), '0'), '.')
         WHEN instr(CAST(amount AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(amount AS TEXT), '0'), '.')
         ELSE CAST(amount AS TEXT) END,
    unit,
    CASE WHEN consumed_weight_g IS NULL THEN NULL
         WHEN instr(lower(CAST(consumed_weight_g AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', consumed_weight_g), '0'), '.')
         WHEN instr(CAST(consumed_weight_g AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(consumed_weight_g AS TEXT), '0'), '.')
         ELSE CAST(consumed_weight_g AS TEXT) END,
    CASE WHEN raw_weight_g IS NULL THEN NULL
         WHEN instr(lower(CAST(raw_weight_g AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', raw_weight_g), '0'), '.')
         WHEN instr(CAST(raw_weight_g AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(raw_weight_g AS TEXT), '0'), '.')
         ELSE CAST(raw_weight_g AS TEXT) END,
    CASE WHEN inventory_deduction_weight_g IS NULL THEN NULL
         WHEN instr(lower(CAST(inventory_deduction_weight_g AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', inventory_deduction_weight_g), '0'), '.')
         WHEN instr(CAST(inventory_deduction_weight_g AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(inventory_deduction_weight_g AS TEXT), '0'), '.')
         ELSE CAST(inventory_deduction_weight_g AS TEXT) END,
    CASE WHEN edible_ratio IS NULL THEN NULL
         WHEN instr(lower(CAST(edible_ratio AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', edible_ratio), '0'), '.')
         WHEN instr(CAST(edible_ratio AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(edible_ratio AS TEXT), '0'), '.')
         ELSE CAST(edible_ratio AS TEXT) END,
    CASE WHEN cooking_yield IS NULL THEN NULL
         WHEN instr(lower(CAST(cooking_yield AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', cooking_yield), '0'), '.')
         WHEN instr(CAST(cooking_yield AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(cooking_yield AS TEXT), '0'), '.')
         ELSE CAST(cooking_yield AS TEXT) END,
    CASE WHEN calories IS NULL THEN NULL
         WHEN instr(lower(CAST(calories AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', calories), '0'), '.')
         WHEN instr(CAST(calories AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(calories AS TEXT), '0'), '.')
         ELSE CAST(calories AS TEXT) END,
    CASE WHEN protein IS NULL THEN NULL
         WHEN instr(lower(CAST(protein AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', protein), '0'), '.')
         WHEN instr(CAST(protein AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(protein AS TEXT), '0'), '.')
         ELSE CAST(protein AS TEXT) END,
    CASE WHEN fat IS NULL THEN NULL
         WHEN instr(lower(CAST(fat AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', fat), '0'), '.')
         WHEN instr(CAST(fat AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(fat AS TEXT), '0'), '.')
         ELSE CAST(fat AS TEXT) END,
    CASE WHEN carbohydrate IS NULL THEN NULL
         WHEN instr(lower(CAST(carbohydrate AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', carbohydrate), '0'), '.')
         WHEN instr(CAST(carbohydrate AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(carbohydrate AS TEXT), '0'), '.')
         ELSE CAST(carbohydrate AS TEXT) END,
    CASE WHEN fiber IS NULL THEN NULL
         WHEN instr(lower(CAST(fiber AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', fiber), '0'), '.')
         WHEN instr(CAST(fiber AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(fiber AS TEXT), '0'), '.')
         ELSE CAST(fiber AS TEXT) END,
    CASE WHEN sodium IS NULL THEN NULL
         WHEN instr(lower(CAST(sodium AS TEXT)), 'e') > 0
         THEN rtrim(rtrim(printf('%!.1074f', sodium), '0'), '.')
         WHEN instr(CAST(sodium AS TEXT), '.') > 0
         THEN rtrim(rtrim(CAST(sodium AS TEXT), '0'), '.')
         ELSE CAST(sodium AS TEXT) END,
    source_grade, confidence, transaction_id
FROM migration_003_meal_items;

INSERT INTO pending_inventory_links (
    id, meal_item_id, candidate_json, confidence, status,
    created_at, resolved_at, transaction_id
)
SELECT
    id, meal_item_id, candidate_json, confidence, status,
    created_at, resolved_at, transaction_id
FROM migration_003_pending_inventory_links;

UPDATE pantry_movements
SET linked_meal_id = (
    SELECT linked_meal_id
    FROM migration_003_pantry_meal_links
    WHERE migration_003_pantry_meal_links.id = pantry_movements.id
)
WHERE id IN (SELECT id FROM migration_003_pantry_meal_links);

DROP TABLE migration_003_pending_inventory_links;
DROP TABLE migration_003_pantry_meal_links;
DROP TABLE migration_003_meal_items;
DROP TABLE migration_003_meals;

CREATE INDEX idx_meals_occurred_at ON meals (occurred_at, id);
CREATE INDEX idx_meal_items_meal_id ON meal_items (meal_id, id);
