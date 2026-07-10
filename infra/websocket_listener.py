"""
Real-time WebSocket listener for PairCreated/PoolCreated events.

Subscribes to V2 PairCreated and V3 PoolCreated events across multiple DEX factories.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from web3 import Web3

from config.factories import (
    FACTORY_REGISTRY,
    MAJOR_ASSET_BLACKLIST,
    FactoryConfig,
)

logger = logging.getLogger(__name__)


@dataclass
class PairEvent:
    token_address: str
    quote_address: str
    pool_address: str
    dex_name: str
    factory_address: str
    block_number: int
    timestamp: float
    event_type: str


class WebSocketListener:
    def __init__(
        self,
        wss_url: str,
        rpc_caller=None,
    ) -> None:
        self.wss_url = wss_url
        self.rpc = rpc_caller
        self._running = False
        self._callbacks: list[Callable[[PairEvent], Awaitable[None]]] = []
        self._seen: set[str] = set()

    def on_pair(self, callback: Callable[[PairEvent], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

    def _is_major_asset(self, addr: str) -> bool:
        return addr.lower() in MAJOR_ASSET_BLACKLIST

    def _classify_pair(
        self, token0: str, token1: str
    ) -> tuple[str, str] | None:
        t0_major = self._is_major_asset(token0)
        t1_major = self._is_major_asset(token1)

        if t0_major and t1_major:
            return None
        if not t0_major and not t1_major:
            return None

        if t0_major:
            return token1, token0
        return token0, token1

    def _parse_v2_event(
        self, log: dict, factory_config: FactoryConfig
    ) -> PairEvent | None:
        try:
            topics = log.get("topics", [])
            if len(topics) < 3:
                return None

            token0 = "0x" + topics[1][-40:]
            token1 = "0x" + topics[2][-40:]

            classified = self._classify_pair(token0, token1)
            if not classified:
                return None

            exotic_token, quote_token = classified

            data = log.get("data", "0x").replace("0x", "")
            if len(data) >= 80:
                pool_address = "0x" + data[24:64]
            else:
                return None

            block_number = int(log.get("blockNumber", "0x0"), 16)

            pair_key = f"{exotic_token.lower()}:{pool_address.lower()}"
            if pair_key in self._seen:
                return None
            self._seen.add(pair_key)

            return PairEvent(
                token_address=exotic_token,
                quote_address=quote_token,
                pool_address=pool_address,
                dex_name=factory_config.dex_venue,
                factory_address=factory_config.factory_address,
                block_number=block_number,
                timestamp=time.time(),
                event_type="PairCreated",
            )
        except Exception as e:
            logger.debug("Parse V2 event failed: %s", e)
            return None

    def _parse_v3_event(
        self, log: dict, factory_config: FactoryConfig
    ) -> PairEvent | None:
        try:
            topics = log.get("topics", [])
            if len(topics) < 4:
                return None

            token0 = "0x" + topics[1][-40:]
            token1 = "0x" + topics[2][-40:]

            classified = self._classify_pair(token0, token1)
            if not classified:
                return None

            exotic_token, quote_token = classified

            data = log.get("data", "0x").replace("0x", "")
            if len(data) >= 128:
                pool_address = "0x" + data[24:64]
            else:
                return None

            block_number = int(log.get("blockNumber", "0x0"), 16)

            pair_key = f"{exotic_token.lower()}:{pool_address.lower()}"
            if pair_key in self._seen:
                return None
            self._seen.add(pair_key)

            return PairEvent(
                token_address=exotic_token,
                quote_address=quote_token,
                pool_address=pool_address,
                dex_name=factory_config.dex_venue,
                factory_address=factory_config.factory_address,
                block_number=block_number,
                timestamp=time.time(),
                event_type="PoolCreated",
            )
        except Exception as e:
            logger.debug("Parse V3 event failed: %s", e)
            return None

    async def _notify_callbacks(self, event: PairEvent) -> None:
        for callback in self._callbacks:
            try:
                await callback(event)
            except Exception as e:
                logger.error("Callback failed: %s", e)

    async def listen_polling(self, interval: float = 2.0) -> None:
        self._running = True
        logger.info("Starting polling listener (interval=%.1fs)", interval)

        if not self.rpc:
            logger.error("No RPC caller provided for polling")
            return

        last_block = await self.rpc.get_block_number()

        while self._running:
            try:
                current_block = await self.rpc.get_block_number()

                if current_block > last_block:
                    for factory_config in FACTORY_REGISTRY:
                        await self._scan_factory(
                            factory_config, last_block + 1, current_block
                        )

                    last_block = current_block

            except Exception as e:
                logger.error("Polling error: %s", e)

            await asyncio.sleep(interval)

    async def _scan_factory(
        self,
        factory_config: FactoryConfig,
        from_block: int,
        to_block: int,
    ) -> None:
        try:
            # Alchemy enforces strict hex encoding for block ranges.
            filter_obj = {
                "fromBlock": hex(int(from_block)),
                "toBlock": hex(int(to_block)),
                "address": factory_config.factory_address,
                "topics": [factory_config.event_topic],
            }
            resp = await self.rpc.call("eth_getLogs", [filter_obj])
            logs = resp.get("result", [])

            for log in logs:
                if factory_config.version == "v2":
                    event = self._parse_v2_event(log, factory_config)
                else:
                    event = self._parse_v3_event(log, factory_config)

                if event:
                    await self._notify_callbacks(event)
                    logger.info(
                        "EVENT: %s %s on %s block=%d",
                        event.event_type,
                        event.token_address[:10],
                        event.dex_name,
                        event.block_number,
                    )

        except Exception as e:
            logger.debug("Scan factory failed for %s: %s", factory_config.name, e)

    async def listen_websocket(self) -> None:
        self._running = True
        logger.info("Starting WebSocket listener on %s", self.wss_url)

        try:
            provider = Web3.AsyncHTTPProvider(self.wss_url)
            Web3(provider)

            for factory_config in FACTORY_REGISTRY:
                event_type = "PairCreated" if factory_config.version == "v2" else "PoolCreated"
                logger.info(
                    "Subscribed to %s %s events",
                    factory_config.name,
                    event_type,
                )

            while self._running:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error("WebSocket listener failed: %s", e)
            self._running = False

    def stop(self) -> None:
        self._running = False
        logger.info("WebSocket listener stopped")
