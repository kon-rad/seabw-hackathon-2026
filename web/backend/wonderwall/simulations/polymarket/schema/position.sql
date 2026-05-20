CREATE TABLE IF NOT EXISTS position (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    market_id   INTEGER NOT NULL,
    outcome     TEXT NOT NULL,   -- 'YES' or 'NO' (or outcome_a/outcome_b text)
    shares      REAL NOT NULL DEFAULT 0.0,
    FOREIGN KEY (user_id) REFERENCES user(user_id),
    FOREIGN KEY (market_id) REFERENCES market(market_id),
    UNIQUE(user_id, market_id, outcome)
);
