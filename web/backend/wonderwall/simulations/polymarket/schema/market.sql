CREATE TABLE IF NOT EXISTS market (
    market_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id  INTEGER NOT NULL,
    question    TEXT NOT NULL,
    outcome_a   TEXT NOT NULL DEFAULT 'YES',
    outcome_b   TEXT NOT NULL DEFAULT 'NO',
    -- AMM pool reserves (constant-product: reserve_a * reserve_b = k)
    reserve_a   REAL NOT NULL DEFAULT 100.0,
    reserve_b   REAL NOT NULL DEFAULT 100.0,
    -- Derived from reserves: price_a = reserve_b / (reserve_a + reserve_b)
    resolved    INTEGER NOT NULL DEFAULT 0,
    winning_outcome TEXT DEFAULT NULL,
    close_date  TEXT DEFAULT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (creator_id) REFERENCES user(user_id)
);
