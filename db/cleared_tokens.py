"""
SQLite database manager for cleared tokens (Process A output, Process B input).

Stores tokens that have passed security audit and are ready for trading.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "cleared_tokens.db"


@dataclass
class ClearedToken:
    token_address: str
    symbol: str
    name: str
    dex_name: str
    pair_address: str
    factory_address: str
    token0: str
    token1: str
    liquidity_usd: float
    fee_tier: int = 3000
    cleared_at: float = field(default_factory=time.time)
    audit_is_safe: bool = True
    audit_threats: list[str] = field(default_factory=list)


class ClearedTokensDB:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cleared_tokens (
                    token_address TEXT NOT NULL,
                    dex_name TEXT NOT NULL,
                    pair_address TEXT NOT NULL,
                    factory_address TEXT NOT NULL,
                    token0 TEXT NOT NULL,
                    token1 TEXT NOT NULL,
                    symbol TEXT DEFAULT '',
                    name TEXT DEFAULT '',
                    liquidity_usd REAL DEFAULT 0.0,
                    fee_tier INTEGER DEFAULT 3000,
                    cleared_at REAL NOT NULL,
                    audit_is_safe BOOLEAN DEFAULT 1,
                    audit_threats TEXT DEFAULT '[]',
                    PRIMARY KEY (token_address, dex_name)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cleared_tokens_token
                ON cleared_tokens(token_address)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cleared_tokens_liquidity
                ON cleared_tokens(liquidity_usd DESC)
            """)
            conn.commit()

    def upsert_token(self, token: ClearedToken) -> None:
        with sqlite3.connect(self.db_path) as conn:
            import json
            conn.execute("""
                INSERT OR REPLACE INTO cleared_tokens
                (token_address, dex_name, pair_address, factory_address,
                 token0, token1, symbol, name, liquidity_usd, fee_tier,
                 cleared_at, audit_is_safe, audit_threats)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                token.token_address.lower(),
                token.dex_name,
                token.pair_address.lower(),
                token.factory_address.lower(),
                token.token0.lower(),
                token.token1.lower(),
                token.symbol,
                token.name,
                token.liquidity_usd,
                token.fee_tier,
                token.cleared_at,
                token.audit_is_safe,
                json.dumps(token.audit_threats),
            ))
            conn.commit()

    def get_cleared_tokens(self) -> list[ClearedToken]:
        import json
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM cleared_tokens
                WHERE audit_is_safe = 1
                ORDER BY liquidity_usd DESC
            """).fetchall()

            return [
                ClearedToken(
                    token_address=row["token_address"],
                    dex_name=row["dex_name"],
                    pair_address=row["pair_address"],
                    factory_address=row["factory_address"],
                    token0=row["token0"],
                    token1=row["token1"],
                    symbol=row["symbol"],
                    name=row["name"],
                    liquidity_usd=row["liquidity_usd"],
                    fee_tier=row["fee_tier"],
                    cleared_at=row["cleared_at"],
                    audit_is_safe=row["audit_is_safe"],
                    audit_threats=json.loads(row["audit_threats"]),
                )
                for row in rows
            ]

    def get_token_pairs(self, token_address: str) -> list[ClearedToken]:
        import json
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM cleared_tokens
                WHERE token_address = ? AND audit_is_safe = 1
            """, (token_address.lower(),)).fetchall()

            return [
                ClearedToken(
                    token_address=row["token_address"],
                    dex_name=row["dex_name"],
                    pair_address=row["pair_address"],
                    factory_address=row["factory_address"],
                    token0=row["token0"],
                    token1=row["token1"],
                    symbol=row["symbol"],
                    name=row["name"],
                    liquidity_usd=row["liquidity_usd"],
                    fee_tier=row["fee_tier"],
                    cleared_at=row["cleared_at"],
                    audit_is_safe=row["audit_is_safe"],
                    audit_threats=json.loads(row["audit_threats"]),
                )
                for row in rows
            ]

    def remove_token(self, token_address: str, dex_name: str | None = None) -> None:
        with sqlite3.connect(self.db_path) as conn:
            if dex_name:
                conn.execute(
                    "DELETE FROM cleared_tokens WHERE token_address = ? AND dex_name = ?",
                    (token_address.lower(), dex_name),
                )
            else:
                conn.execute(
                    "DELETE FROM cleared_tokens WHERE token_address = ?",
                    (token_address.lower(),),
                )
            conn.commit()

    def token_count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute("SELECT COUNT(*) FROM cleared_tokens").fetchone()
            return result[0] if result else 0

    def clear_all(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cleared_tokens")
            conn.commit()
