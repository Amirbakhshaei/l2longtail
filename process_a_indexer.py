"""
Process A: Pure Arbitrage Sync Engine

Monitors a static whitelist of established V2 pools for Sync events.
When a Sync fires, stores the updated pool state in the DB for
Process B to evaluate triangular arbitrage opportunities.

No LLM calls. No PairCreated scanning. Pure Sync event monitoring.

Flow:
A1: Sync Event Scanner → A2: Pool State Store
"""
from __future__ import annotations

import asyncio
import logging
import time

from db.cleared_tokens import ClearedToken, ClearedTokensDB
from infra.flea_market_discovery import FleaMarketDiscovery, SyncEvent
from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)


class ProcessAIndexer:
    def __init__(
        self,
        rpc_manager: RPCManager,
        cleared_db: ClearedTokensDB,
        flea_discovery: FleaMarketDiscovery,
        lookback_blocks: int = 50,
    ) -> None:
        self.rpc = rpc_manager
        self.db = cleared_db
        self.flea = flea_discovery
        self.lookback_blocks = lookback_blocks
        self._running = False
        self._events_processed: int = 0

    async def run(self, scan_interval: float = 5.0) -> None:
        self._running = True
        logger.info(
            "Process A: Sync Engine started (%d pools, %d block lookback)",
            self.flea.pool_count,
            self.lookback_blocks,
        )

        while self._running:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error("Sync scan cycle failed: %s", e)

            await asyncio.sleep(scan_interval)

    async def _scan_cycle(self) -> None:
        events = await self.flea.scan_sync_events(
            lookback_blocks=self.lookback_blocks
        )

        for event in events:
            try:
                await self._process_event(event)
            except Exception as e:
                logger.error(
                    "Failed to process Sync for %s: %s",
                    event.pair_address[:10],
                    e,
                )

    async def _process_event(self, event: SyncEvent) -> None:
        self._events_processed += 1

        weth_addr = "0x82af49447d8a07e3bd95bd0d56f35241523fab1"
        usdc_addr = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
        usdt_addr = "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9"

        major_tokens = {weth_addr, usdc_addr, usdt_addr}

        t0 = event.token0.lower()
        t1 = event.token1.lower()

        if t0 in major_tokens and t1 in major_tokens:
            return

        if t0 not in major_tokens and t1 not in major_tokens:
            return

        if t0 in major_tokens:
            exotic_token = t1
        else:
            exotic_token = t0

        cleared_token = ClearedToken(
            token_address=exotic_token,
            symbol="",
            name="",
            dex_name=event.dex,
            pair_address=event.pair_address,
            factory_address="",
            token0=event.token0,
            token1=event.token1,
            liquidity_usd=0.0,
            cleared_at=time.time(),
            audit_is_safe=True,
            audit_threats=[],
        )
        self.db.upsert_token(cleared_token)

        if self._events_processed % 50 == 0:
            logger.info(
                "SYNC ENGINE: %d events processed, last=%s on %s (r0=%d r1=%d)",
                self._events_processed,
                event.pair_address[:10],
                event.dex,
                event.reserve0,
                event.reserve1,
            )

    def stop(self) -> None:
        self._running = False
        logger.info(
            "Process A: Sync Engine stopped (%d events processed)",
            self._events_processed,
        )
