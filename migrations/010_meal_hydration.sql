ALTER TABLE meals
ADD COLUMN total_hydration_ml TEXT CHECK (
    total_hydration_ml IS NULL
    OR (
        typeof(total_hydration_ml) = 'text'
        AND total_hydration_ml <> ''
        AND total_hydration_ml NOT GLOB '*[^0-9.]*'
        AND total_hydration_ml NOT GLOB '*.*.*'
        AND total_hydration_ml NOT LIKE '.%'
        AND total_hydration_ml NOT LIKE '%.'
        AND CAST(total_hydration_ml AS NUMERIC) >= 0
    )
);

ALTER TABLE meal_items
ADD COLUMN hydration_ml TEXT CHECK (
    hydration_ml IS NULL
    OR (
        typeof(hydration_ml) = 'text'
        AND hydration_ml <> ''
        AND hydration_ml NOT GLOB '*[^0-9.]*'
        AND hydration_ml NOT GLOB '*.*.*'
        AND hydration_ml NOT LIKE '.%'
        AND hydration_ml NOT LIKE '%.'
        AND CAST(hydration_ml AS NUMERIC) >= 0
    )
);
