BEGIN TRANSACTION;
CREATE TABLE messages (
                    id INTEGER PRIMARY KEY, sender INTEGER NOT NULL REFERENCES users(id),
                    recipient INTEGER NOT NULL REFERENCES users(id), body TEXT NOT NULL,
                    sent REAL NOT NULL, read_at REAL,
                    nonce TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, UNIQUE(sender, nonce)
                );
CREATE TABLE sessions (
                    digest TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    expires REAL NOT NULL
                );
CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                    last_seen REAL NOT NULL DEFAULT 0
                );
CREATE INDEX messages_pair ON messages(sender, recipient, id);
CREATE INDEX sessions_expiry ON sessions(expires);
COMMIT;
