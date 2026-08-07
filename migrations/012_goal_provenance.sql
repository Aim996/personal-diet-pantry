ALTER TABLE nutrition_goal_profiles
ADD COLUMN goal_source TEXT NOT NULL
    DEFAULT 'configuration_default'
    CHECK (goal_source IN ('configuration_default', 'user_confirmed'));

ALTER TABLE nutrition_goal_profiles
ADD COLUMN confirmed_at TEXT
    CHECK (
        (goal_source = 'configuration_default' AND confirmed_at IS NULL)
        OR
        (
            goal_source = 'user_confirmed'
            AND confirmed_at IS NOT NULL
            AND COALESCE(
                (
                    length(confirmed_at) = 20
                    AND strftime(
                        '%Y-%m-%dT%H:%M:%SZ',
                        confirmed_at,
                        '+0 seconds'
                    ) = confirmed_at
                )
                OR
                (
                    length(confirmed_at) >= 22
                    AND substr(confirmed_at, 20, 1) = '.'
                    AND substr(confirmed_at, -1, 1) = 'Z'
                    AND substr(
                        confirmed_at,
                        21,
                        length(confirmed_at) - 21
                    ) NOT GLOB '*[^0-9]*'
                    AND strftime(
                        '%Y-%m-%dT%H:%M:%S',
                        confirmed_at,
                        '+0 seconds'
                    ) = substr(confirmed_at, 1, 19)
                ),
                0
            )
        )
    );

UPDATE nutrition_goal_profiles
SET
    goal_source = CASE
        WHEN transaction_id IS NULL THEN 'configuration_default'
        ELSE 'user_confirmed'
    END,
    confirmed_at = CASE
        WHEN transaction_id IS NULL THEN NULL
        ELSE updated_at
    END;
