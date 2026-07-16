"""
Process A: Real-Time Sync Engine (WSS Transport)

Ingests sequencer `Sync` events over WebSockets and instantly forwards the
updated pool/reserve payloads to Process B (the graph sniper) via an
asyncio.Queue. No HTTP polling — discovery latency collapses to sequencer
propagation time.

Flow:
    WSS Sync stream → parse reserves → enqueue for Process B (+ DB upsert)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from db.cleared_tokens import ClearedToken, ClearedTokensDB
from infra.flea_market_discovery import SyncEvent, V3StateEvent
from infra.rpc_manager import RPCManager
from infra.websocket_listener import WebSocketListener

logger = logging.getLogger(__name__)

WETH_ADDRESS = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"


class ProcessAIndexer:
    def __init__(
        self,
        rpc_manager: RPCManager,
        cleared_db: ClearedTokensDB,
        websocket_listener: WebSocketListener,
        sync_queue: asyncio.Queue[SyncEvent] | None = None,
        flea_discovery=None,
    ) -> None:
        self.rpc = rpc_manager
        self.db = cleared_db
        self.listener = websocket_listener
        self.queue = sync_queue or asyncio.Queue()
        self.flea = flea_discovery
        self._running = False
        self._events_processed: int = 0
        self._resync_misses: int = 0
        self._callbacks: list[Callable[[SyncEvent], Awaitable[None]]] = []

        self.listener.on_sync(self._handle_sync)
        self.listener.on_v3_swap(self._handle_v3)

    def on_sync(self, callback: Callable[[SyncEvent], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

    async def run(self) -> None:
        """Await the WSS stream; never returns until stop()."""
        self._running = True
        logger.info(
            "Process A: WSS Sync Engine started (queue=%s)",
            "live" if self.queue else "none",
        )
        try:
            await self.listener.listen()
        finally:
            self._running = False

    async def _handle_sync(self, event: SyncEvent) -> None:
        self._events_processed += 1

        token0, token1, dex = await self._resolve_meta(event)
        event.token0 = token0
        event.token1 = token1
        event.dex = dex

        self._maybe_upsert(event, token0, token1, dex)

        await self.queue.put(event)

        for cb in self._callbacks:
            try:
                await cb(event)
            except Exception as e:  # noqa: BLE001
                logger.error("Process A downstream callback failed: %s", e)

        if self._events_processed % 50 == 0:
            logger.info(
                "SYNC ENGINE: %d events | last=%s reserves=(%d,%d)",
                self._events_processed,
                event.pair_address[:10],
                event.reserve0,
                event.reserve1,
            )

    async def _resolve_meta(self, event: SyncEvent) -> tuple[str, str, str]:
        if self.flea is not None:
            meta = self.flea.get_pool_meta(event.pair_address)
            if meta is not None:
                return meta.token0, meta.token1, meta.dex
        return "", "", ""

    def _maybe_upsert(
        self, event: SyncEvent, token0: str, token1: str, dex: str
    ) -> None:
        if not token0 or not token1:
            return
        exotic = self._pick_exotic(token0, token1)
        if not exotic:
            return
        self.db.upsert_token(
            ClearedToken(
                token_address=exotic,
                symbol="",
                name="",
                dex_name=dex,
                pair_address=event.pair_address,
                factory_address="",
                token0=token0,
                token1=token1,
                liquidity_usd=0.0,
                cleared_at=time.time(),
                audit_is_safe=True,
                audit_threats=[],
            )
        )

    @staticmethod
    def _pick_exotic(token0: str, token1: str) -> str:
        t0, t1 = token0.lower(), token1.lower()
        if t0 == WETH_ADDRESS.lower():
            return token1
        if t1 == WETH_ADDRESS.lower():
            return token0
        return ""

    def stop(self) -> None:
        self._running = False
        self.listener.stop()
        logger.info(
            "Process A: Sync Engine stopped (%d events processed)",
            self._events_processed,
        )

    # ------------------------------------------------------------------ #
    # V3 Swap ingestion
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_v3_log(log: dict) -> V3StateEvent | None:
        """Decode a Uniswap/Camelot v3 Swap log into a V3StateEvent.

        Non-indexed data layout (each field is a 32-byte ABI word, i.e. 64
        hex chars, right-aligned):
            amount0      int256  bytes [0:32]
            amount1      int256  bytes [32:64]
            sqrtPriceX96 uint160  bytes [64:96]
            liquidity    uint128  bytes [96:128]
            tick         int24    bytes [128:160]
        """
        try:
            pool = (log.get("address") or "").lower()
            data_hex = (log.get("data") or "0x").replace("0x", "")
            if len(data_hex) < 320:
                return None

            # 64-hex-char windows (32-byte words).
            sqrt_price_x96 = int(data_hex[128:192], 16)
            liquidity = int(data_hex[192:256], 16)
            raw_tick = int(data_hex[256:320], 16)
            if raw_tick >= 2**23:
                raw_tick -= 2**24

            block_number = int(log.get("blockNumber", "0x0"), 16)
            dedup_key = f"{pool}:{block_number}:{sqrt_price_x96}:{liquidity}"
            if getattr(ProcessAIndexer, "_v3_seen", None) is None:
                ProcessAIndexer._v3_seen = set()
            if dedup_key in ProcessAIndexer._v3_seen:
                return None
            ProcessAIndexer._v3_seen.add(dedup_key)
            if len(ProcessAIndexer._v3_seen) > 200_000:
                ProcessAIndexer._v3_seen.clear()

            return V3StateEvent(
                pool_address=pool,
                token0="",
                token1="",
                sqrt_price_x96=sqrt_price_x96,
                tick=raw_tick,
                liquidity=liquidity,
                block_number=block_number,
                dex="",
                timestamp=time.time(),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to parse V3 Swap log: %s", e)
            return None

    async def _handle_v3(self, log: dict) -> None:
        event = self._parse_v3_log(log)
        if event is None:
            return
        self._events_processed += 1

        token0, token1, dex = await self._resolve_meta_v3(event)
        event.token0 = token0
        event.token1 = token1
        event.dex = dex

        await self.queue.put(event)

        for cb in self._callbacks:
            try:
                await cb(event)
            except Exception as e:  # noqa: BLE001
                logger.error("Process A V3 downstream callback failed: %s", e)

    async def _resolve_meta_v3(self, event: V3StateEvent) -> tuple[str, str, str]:
        if self.flea is not None:
            meta = self.flea.get_pool_meta(event.pool_address)
            if meta is not None:
                return meta.token0, meta.token1, meta.dex
        return "", "", ""
