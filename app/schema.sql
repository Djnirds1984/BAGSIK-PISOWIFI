-- BAGSIK PISOWIFI - SQLite schema
-- Money is always stored in centavos (integers). Timestamps are ISO-8601 UTC.

CREATE TABLE IF NOT EXISTS admin_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    display_name  TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    price_centavos  INTEGER NOT NULL,
    duration_minutes INTEGER NOT NULL,
    data_cap_mb     INTEGER NOT NULL DEFAULT 0,   -- 0 = unlimited
    rate_down_kbps  INTEGER NOT NULL DEFAULT 0,   -- 0 = unshaped
    rate_up_kbps    INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT    NOT NULL DEFAULT '',
    plan_id     INTEGER REFERENCES plans(id),
    quantity    INTEGER NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'admin',   -- admin | kiosk
    created_by  TEXT    NOT NULL DEFAULT 'system',
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS vouchers (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    code              TEXT    NOT NULL UNIQUE,
    plan_id           INTEGER REFERENCES plans(id),
    price_centavos    INTEGER NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'unused', -- unused | active | used | expired | void
    source            TEXT    NOT NULL DEFAULT 'admin',  -- admin | kiosk
    batch_id          INTEGER REFERENCES batches(id),
    note              TEXT    NOT NULL DEFAULT '',
    created_at        TEXT    NOT NULL,
    redeemed_at       TEXT,
    voided_at         TEXT,
    session_id        INTEGER
);

CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mac         TEXT    NOT NULL UNIQUE,
    ip          TEXT,
    label       TEXT    NOT NULL DEFAULT '',
    first_seen  TEXT    NOT NULL,
    last_seen   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT    NOT NULL DEFAULT 'voucher', -- voucher | coin
    voucher_id      INTEGER REFERENCES vouchers(id),
    plan_id         INTEGER REFERENCES plans(id),
    plan_name       TEXT    NOT NULL DEFAULT '',
    mac             TEXT    NOT NULL,
    ip              TEXT,
    status          TEXT    NOT NULL DEFAULT 'active',  -- active | paused | expired | terminated
    started_at      TEXT    NOT NULL,
    ends_at         TEXT    NOT NULL,
    ended_at        TEXT,
    paused_at       TEXT,
    time_cap_seconds INTEGER NOT NULL DEFAULT 0,
    data_cap_bytes  INTEGER NOT NULL DEFAULT 0,
    bytes_used      INTEGER NOT NULL DEFAULT 0,
    last_rx_bytes   INTEGER NOT NULL DEFAULT 0,
    last_tx_bytes   INTEGER NOT NULL DEFAULT 0,
    rate_down_kbps  INTEGER NOT NULL DEFAULT 0,
    rate_up_kbps    INTEGER NOT NULL DEFAULT 0,
    coin_centavos   INTEGER NOT NULL DEFAULT 0,
    end_reason      TEXT    NOT NULL DEFAULT '',
    created_by      TEXT    NOT NULL DEFAULT 'portal'
);

CREATE TABLE IF NOT EXISTS coin_credits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    centavos    INTEGER NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'gpio',   -- gpio | mock | admin
    status      TEXT    NOT NULL DEFAULT 'pending', -- pending | consumed | void
    pulses      INTEGER NOT NULL DEFAULT 0,
    session_id  INTEGER REFERENCES sessions(id),
    voucher_id  INTEGER REFERENCES vouchers(id),
    created_at  TEXT    NOT NULL,
    consumed_at TEXT,
    note        TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT 'system',
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_vouchers_status   ON vouchers(status);
CREATE INDEX IF NOT EXISTS idx_vouchers_batch    ON vouchers(batch_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status   ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_mac      ON sessions(mac);
CREATE INDEX IF NOT EXISTS idx_sessions_started  ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_coin_credits_ts   ON coin_credits(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_ts          ON audit_logs(created_at);
