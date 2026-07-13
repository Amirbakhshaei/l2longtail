"""
Process A: Background Indexer (Fast Discovery)

Lightweight, fast, deterministic. Runs in the background to build a whitelist.
Finds new tokens, applies liquidity/age/blacklist filters, and stores to DB.

No LLM calls. No security audits. Process A is a pure filter gate.

Flow:
A1: Flea Market Scanner → A2: Filter Gate → A3: Database Store
"""
from __future__ import annotations

import asyncio
import logging
import time

from config.factories import (
    MAJOR_ASSET_BLACKLIST,
    MAX_LIQUIDITY_USD,
    MIN_LIQUIDITY_USD,
)
from db.cleared_tokens import ClearedToken, ClearedTokensDB
from infra.flea_market_discovery import FleaMarketDiscovery
from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)


class ProcessAIndexer:
    def __init__(
        self,
        rpc_manager: RPCManager,
        cleared_db: ClearedTokensDB,
        flea_discovery: FleaMarketDiscovery,
    ) -> None:
        self.rpc = rpc_manager
        self.db = cleared_db
        self.flea = flea_discovery
        self._running = False

    async def run(self, scan_interval: float = 60.0) -> None:
        self._running = True
        logger.info("Process A: Indexer started")

        while self._running:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error("Indexer scan cycle failed: %s", e)

            await asyncio.sleep(scan_interval)

    async def _scan_cycle(self) -> None:
        targets = await self.flea.scan_recent_pairs(lookback_blocks=1000)
        logger.info("Process A: Found %d new targets", len(targets))

        for target in targets:
            try:
                await self._process_target(target)
            except Exception as e:
                logger.error(
                    "Failed to process target %s: %s",
                    target.token_address[:10],
                    e,
                )

    async def _process_target(self, target) -> None:
        from agents.state import FleaMarketTarget

        if not isinstance(target, FleaMarketTarget):
            return

        logger.info(
            "Processing %s on %s (liq=$%.0f)",
            target.token_address[:10],
            target.dex_venue_name,
            target.initial_liquidity_usd,
        )

        if not self._passes_filter_gate(target):
            return

        from infra.create2 import compute_v2_pair_address

        pair = compute_v2_pair_address(
            target.dex_venue_name,
            target.token_address,
            target.quote_address,
        )
        if not pair:
            logger.debug(
                "No pair address for %s on %s",
                target.token_address[:10],
                target.dex_venue_name,
            )
            return

        cleared_token = ClearedToken(
            token_address=target.token_address,
            symbol="",
            name="",
            dex_name=target.dex_venue_name,
            pair_address=pair.pair_address,
            factory_address=pair.factory,
            token0=pair.token0,
            token1=pair.token1,
            liquidity_usd=target.initial_liquidity_usd,
            cleared_at=time.time(),
            audit_is_safe=True,
            audit_threats=[],
        )
        self.db.upsert_token(cleared_token)

        logger.info(
            "CLEARED: %s on %s (liq=$%.0f)",
            target.token_address[:10],
            target.dex_venue_name,
            target.initial_liquidity_usd,
        )

    def _passes_filter_gate(self, target) -> bool:
        if target.token_address.lower() in MAJOR_ASSET_BLACKLIST:
            return False

        if target.initial_liquidity_usd < MIN_LIQUIDITY_USD:
            return False

        if target.initial_liquidity_usd > MAX_LIQUIDITY_USD:
            return False

        return True

    def stop(self) -> None:
        self._running = False
        logger.info("Process A: Indexer stopped")
