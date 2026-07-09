from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


@dataclass
class CachedSource:
    is_verified: bool
    raw_source: str
    minified_source: str


@dataclass
class CachedAudit:
    is_safe: bool
    threats: list[str]
    minified_source: str


class ContractCache:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        schema_path = Path(__file__).parent / "schema.sql"
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(schema_path.read_text())
            await db.commit()

    async def get_source(self, token_address: str, ttl_hours: int = 24) -> CachedSource | None:
        cutoff = time.time() - (ttl_hours * 3600)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT is_verified, raw_source, minified_source "
                "FROM contract_cache WHERE token_address = ? AND fetched_at > ?",
                (token_address.lower(), cutoff),
            )
            row = await cursor.fetchone()
            if row:
                return CachedSource(
                    is_verified=bool(row[0]),
                    raw_source=row[1] or "",
                    minified_source=row[2] or "",
                )
        return None

    async def store_source(
        self, token_address: str, is_verified: bool, raw: str, minified: str
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO contract_cache VALUES (?, ?, ?, ?, ?)",
                (token_address.lower(), int(is_verified), raw, minified, time.time()),
            )
            await db.commit()

    async def get_audit(self, token_address: str, ttl_hours: int = 24) -> CachedAudit | None:
        cutoff = time.time() - (ttl_hours * 3600)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT ac.is_safe, ac.threats, cc.minified_source "
                "FROM audit_cache ac "
                "JOIN contract_cache cc ON ac.token_address = cc.token_address "
                "WHERE ac.token_address = ? AND ac.audited_at > ?",
                (token_address.lower(), cutoff),
            )
            row = await cursor.fetchone()
            if row:
                return CachedAudit(
                    is_safe=bool(row[0]),
                    threats=json.loads(row[1]) if row[1] else [],
                    minified_source=row[2] or "",
                )
        return None

    async def store_audit(
        self,
        token_address: str,
        is_safe: bool,
        threats: list[str],
        minified_source: str,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO audit_cache VALUES (?, ?, ?, ?)",
                (token_address.lower(), int(is_safe), json.dumps(threats), time.time()),
            )
            await db.commit()

    async def insert_execution_log(
        self,
        run_id: str,
        token_address: str,
        pool_address: str,
        status: str,
        net_profit_usd: float | None,
        tx_hash: str | None,
        reason: str | None,
        dry_run: bool,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO execution_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    token_address.lower(),
                    pool_address.lower(),
                    status,
                    net_profit_usd,
                    tx_hash,
                    reason,
                    int(dry_run),
                    time.time(),
                ),
            )
            await db.commit()

    async def purge_expired(self, ttl_hours: int = 24) -> int:
        cutoff = time.time() - (ttl_hours * 3600)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM contract_cache WHERE fetched_at < ?", (cutoff,)
            )
            deleted_sources = cursor.rowcount
            cursor = await db.execute(
                "DELETE FROM audit_cache WHERE audited_at < ?", (cutoff,)
            )
            deleted_audits = cursor.rowcount
            cursor = await db.execute(
                "DELETE FROM token_flea_cache WHERE cached_at < ?", (cutoff,)
            )
            deleted_flea = cursor.rowcount
            await db.commit()
        return deleted_sources + deleted_audits + deleted_flea

    async def is_known_bad_token(self, token_address: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM token_flea_cache WHERE token_address = ?",
                (token_address.lower(),),
            )
            return await cursor.fetchone() is not None

    async def mark_bad_token(self, token_address: str, reason: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO token_flea_cache VALUES (?, ?, ?)",
                (token_address.lower(), reason, time.time()),
            )
            await db.commit()
