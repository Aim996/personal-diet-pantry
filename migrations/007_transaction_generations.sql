ALTER TABLE transactions
ADD COLUMN generation INTEGER NOT NULL DEFAULT 0
CHECK (typeof(generation) = 'integer' AND generation >= 0);
