CREATE TABLE IF NOT EXISTS trade (
    trade_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    market_id   INTEGER NOT NULL,
    side        TEXT NOT NULL,       -- 'buy' or 'sell'
    outcome     TEXT NOT NULL,       -- which outcome was traded
    shares      REAL NOT NULL,       -- number of shares
    price       REAL NOT NULL,       -- effective price per share
    cost        REAL NOT NULL,       -- total cost (shares * price)
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES user(user_id),
    FOREIGN KEY (market_id) REFERENCES market(market_id)
);
