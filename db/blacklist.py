from __future__ import annotations

import time

import aiosqlite


class BlacklistDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def contains(self, token_address: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM blacklist WHERE token_address = ?",
                (token_address.lower(),),
            )
            return await cursor.fetchone() is not None

    async def add(self, token_address: str, reason: str = "") -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO blacklist VALUES (?, ?, ?)",
                (token_address.lower(), reason, time.time()),
            )
            await db.commit()

    async def remove(self, token_address: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM blacklist WHERE token_address = ?",
                (token_address.lower(),),
            )
            await db.commit()

    async def list_all(self) -> list[dict[str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT token_address, reason FROM blacklist")
            rows = await cursor.fetchall()
        return [{"token_address": r[0], "reason": r[1]} for r in rows]

    async def seed_from_list(self, entries: list[dict[str, str]]) -> int:
        count = 0
        async with aiosqlite.connect(self.db_path) as db:
            for entry in entries:
                await db.execute(
                    "INSERT OR IGNORE INTO blacklist VALUES (?, ?, ?)",
                    (entry["token_address"].lower(), entry.get("reason", ""), time.time()),
                )
                count += 1
            await db.commit()
        return count
