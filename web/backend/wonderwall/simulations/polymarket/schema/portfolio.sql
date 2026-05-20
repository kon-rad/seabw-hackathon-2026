CREATE TABLE IF NOT EXISTS portfolio (
    portfolio_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    -- Cash balance in USD
    balance     REAL NOT NULL DEFAULT 1000.0,
    FOREIGN KEY (user_id) REFERENCES user(user_id)
);
