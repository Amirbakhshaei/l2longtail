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
from infra.flea_market_discovery import SyncEvent
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
