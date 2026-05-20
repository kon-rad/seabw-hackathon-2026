CREATE TABLE IF NOT EXISTS market_comment (
    comment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (market_id) REFERENCES market(market_id),
    FOREIGN KEY (user_id) REFERENCES user(user_id)
);
