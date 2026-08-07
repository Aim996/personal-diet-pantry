ALTER TABLE pantry_batches
ADD COLUMN price_minor INTEGER CHECK (
    price_minor IS NULL
    OR (typeof(price_minor) = 'integer' AND price_minor >= 0)
);

ALTER TABLE pantry_batches
ADD COLUMN currency TEXT CHECK (
    currency IS NULL
    OR (
        length(currency) = 3
        AND currency = upper(currency)
        AND currency NOT GLOB '*[^A-Z]*'
    )
);

ALTER TABLE pantry_batches
ADD COLUMN remaining_cost_minor INTEGER CHECK (
    remaining_cost_minor IS NULL
    OR (
        typeof(remaining_cost_minor) = 'integer'
        AND remaining_cost_minor >= 0
        AND (price_minor IS NULL OR remaining_cost_minor <= price_minor)
    )
);

ALTER TABLE pantry_movements
ADD COLUMN waste_category TEXT CHECK (
    waste_category IS NULL
    OR waste_category IN (
        'spoilage',
        'expired',
        'overprepared',
        'quality',
        'other',
        'unspecified'
    )
);

CREATE TABLE pantry_cost_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pantry_batch_id INTEGER NOT NULL
        REFERENCES pantry_batches(id) ON DELETE RESTRICT,
    pantry_movement_id INTEGER NOT NULL UNIQUE
        REFERENCES pantry_movements(id) ON DELETE RESTRICT,
    allocation_kind TEXT NOT NULL CHECK (
        allocation_kind IN ('consume', 'waste', 'adjustment')
    ),
    quantity REAL NOT NULL CHECK (
        typeof(quantity) IN ('integer', 'real') AND quantity > 0
    ),
    unit TEXT NOT NULL CHECK (length(trim(unit)) BETWEEN 1 AND 24),
    cost_minor INTEGER NOT NULL CHECK (
        typeof(cost_minor) = 'integer' AND cost_minor >= 0
    ),
    currency TEXT NOT NULL CHECK (
        length(currency) = 3
        AND currency = upper(currency)
        AND currency NOT GLOB '*[^A-Z]*'
    ),
    allocated_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', allocated_at, '+0 seconds') = allocated_at,
        0
    )),
    transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT
);

CREATE INDEX idx_pantry_cost_allocations_time_currency
ON pantry_cost_allocations (allocated_at, currency, allocation_kind, id);

CREATE INDEX idx_pantry_cost_allocations_batch
ON pantry_cost_allocations (pantry_batch_id, id);

CREATE INDEX idx_pantry_movements_waste
ON pantry_movements (waste_category, created_at, id)
WHERE movement_type IN ('discard', 'expire');
