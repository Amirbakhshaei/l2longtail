"""
Whitelist Sync Event Monitor.

Monitors a static whitelist of established V2 pools for Sync events.
When a Sync fires (reserve update from a trade), parses the updated reserves
and passes the pool to Process B for triangular arbitrage evaluation.

No PairCreated scanning. No factory looping. Pure Sync event monitoring.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

V2_SYNC_TOPIC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"

# Uniswap/Camelot v3 Swap event: Swap(address,address,int256,int256,uint160,uint128,int24)
V3_SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

V3_DEXES = {"uniswap_v3", "camelot_v3"}


@dataclass
class WhitelistPool:
    pair_address: str
    token0: str
    token1: str
    dex: str

    @property
    def is_v3(self) -> bool:
        return self.dex in V3_DEXES


@dataclass
class SyncEvent:
    pair_address: str
    token0: str
    token1: str
    reserve0: int
    reserve1: int
    block_number: int
    dex: str
    timestamp: float


@dataclass
class V3StateEvent:
    """Concentrated-liquidity pool state update.

    Carries the current ``sqrtPriceX96``, ``tick`` and ``liquidity`` of a
    Uniswap/Camelot v3 pool, emitted on every ``Swap`` / ``Burn`` / ``Mint``
    that moves the active range. The engine feeds this straight into
    ``DirectedGraph.update_v3_state`` to re-price the V3 edge.
    """

    pool_address: str
    token0: str
    token1: str
    sqrt_price_x96: int
    tick: int
    liquidity: int
    block_number: int
    dex: str
    timestamp: float


class FleaMarketDiscovery:
    def __init__(
        self,
        rpc_manager: RPCManager,
        whitelist_path: str = "config/whitelist.json",
    ) -> None:
        self.rpc = rpc_manager
        self._whitelist: list[WhitelistPool] = []
        self._whitelist_path = whitelist_path
        self._seen_blocks: dict[str, int] = {}
        self._load_whitelist()

    def _load_whitelist(self) -> None:
        path = Path(self._whitelist_path)
        if not path.exists():
            logger.error("Whitelist file not found: %s", path)
            return

        try:
            raw = json.loads(path.read_text())
            self._whitelist = [
                WhitelistPool(
                    pair_address=entry["pair_address"].lower(),
                    token0=entry["token0"].lower(),
                    token1=entry["token1"].lower(),
                    dex=entry["dex"],
                )
                for entry in raw
            ]
            logger.info("Loaded %d whitelisted pools", len(self._whitelist))
        except Exception as e:
            logger.error("Failed to load whitelist: %s", e)

    @property
    def whitelisted_addresses(self) -> list[str]:
        return [p.pair_address for p in self._whitelist]

    @property
    def v3_addresses(self) -> list[str]:
        return [p.pair_address for p in self._whitelist if p.is_v3]

    @property
    def pool_count(self) -> int:
        return len(self._whitelist)

    def get_pool_meta(self, pair_address: str) -> WhitelistPool | None:
        lower = pair_address.lower()
        for pool in self._whitelist:
            if pool.pair_address == lower:
                return pool
        return None

    async def scan_sync_events(
        self, lookback_blocks: int = 50
    ) -> list[SyncEvent]:
        if not self._whitelist:
            logger.warning("Empty whitelist — nothing to scan")
            return []

        current_block = await self.rpc.get_block_number()
        from_block = max(current_block - lookback_blocks, 0)

        filter_obj = {
            "fromBlock": hex(int(from_block)),
            "toBlock": hex(int(current_block)),
            "address": self.whitelisted_addresses,
            "topics": [V2_SYNC_TOPIC],
        }

        try:
            resp = await self.rpc.call("eth_getLogs", [filter_obj])
        except Exception as e:
            logger.error("eth_getLogs failed: %s", e)
            return []

        if "error" in resp:
            logger.error("RPC error on eth_getLogs: %s", resp["error"])
            return []

        logs = resp.get("result", [])
        events: list[SyncEvent] = []

        for log in logs:
            event = self._parse_sync_log(log)
            if event:
                events.append(event)

        if events:
            logger.info(
                "SYNC: %d events across %d blocks (blocks %d-%d)",
                len(events),
                lookback_blocks,
                from_block,
                current_block,
            )

        return events

    def _parse_sync_log(self, log: dict) -> SyncEvent | None:
        try:
            pair_address = log.get("address", "").lower()

            meta = self.get_pool_meta(pair_address)
            if not meta:
                return None

            block_number = int(log.get("blockNumber", "0x0"), 16)

            dedup_key = f"{pair_address}:{block_number}"
            if dedup_key in self._seen_blocks:
                return None
            self._seen_blocks[dedup_key] = block_number

            if len(self._seen_blocks) > 50000:
                oldest = min(self._seen_blocks.values())
                self._seen_blocks = {
                    k: v for k, v in self._seen_blocks.items() if v > oldest
                }

            data_hex = log.get("data", "0x")
            if data_hex.startswith("0x"):
                data_hex = data_hex[2:]

            if len(data_hex) < 128:
                logger.debug(
                    "Sync data too short for %s: %d chars",
                    pair_address[:10],
                    len(data_hex),
                )
                return None

            reserve0 = int(data_hex[0:64], 16)
            reserve1 = int(data_hex[64:128], 16)

            if reserve0 == 0 and reserve1 == 0:
                return None

            return SyncEvent(
                pair_address=pair_address,
                token0=meta.token0,
                token1=meta.token1,
                reserve0=reserve0,
                reserve1=reserve1,
                block_number=block_number,
                dex=meta.dex,
                timestamp=time.time(),
            )

        except Exception as e:
            logger.debug("Failed to parse Sync log: %s", e)
            return None

    async def stop(self) -> None:
        pass
