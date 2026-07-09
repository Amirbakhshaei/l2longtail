CREATE TABLE IF NOT EXISTS contract_cache (
    token_address TEXT PRIMARY KEY,
    is_verified   INTEGER NOT NULL,
    raw_source    TEXT,
    minified_source TEXT,
    fetched_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_cache (
    token_address TEXT PRIMARY KEY,
    is_safe       INTEGER NOT NULL,
    threats       TEXT,
    audited_at    REAL NOT NULL,
    FOREIGN KEY (token_address) REFERENCES contract_cache(token_address)
);

CREATE TABLE IF NOT EXISTS blacklist (
    token_address TEXT PRIMARY KEY,
    reason        TEXT,
    added_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_log (
    run_id        TEXT PRIMARY KEY,
    token_address TEXT NOT NULL,
    pool_address  TEXT NOT NULL,
    status        TEXT NOT NULL,
    net_profit_usd REAL,
    tx_hash       TEXT,
    reason        TEXT,
    dry_run       INTEGER NOT NULL,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS token_flea_cache (
    token_address TEXT PRIMARY KEY,
    reason        TEXT NOT NULL,
    cached_at     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cache_ttl ON contract_cache(fetched_at);
CREATE INDEX IF NOT EXISTS idx_audit_ttl ON audit_cache(audited_at);
CREATE INDEX IF NOT EXISTS idx_exec_status ON execution_log(status);
CREATE INDEX IF NOT EXISTS idx_flea_ttl ON token_flea_cache(cached_at);
