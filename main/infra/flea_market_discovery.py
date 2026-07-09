from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from web3 import Web3

from agents.state import FleaMarketTarget
from config.factories import (
    FACTORY_REGISTRY,
    MAJOR_ASSET_BLACKLIST,
    MAX_LIQUIDITY_USD,
    MAX_TOKEN_AGE_HOURS,
    MIN_LIQUIDITY_USD,
    FactoryConfig,
)
from db.cache import ContractCache
from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

WETH_ADDRESS = "0x82aF49447D8a07e3bd95bD0d56f35241523fBab1".lower()
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831".lower()

V2_PAIR_CREATED_ABI = [
    {
        "anonymous": False,
        "name": "PairCreated",
        "type": "event",
        "inputs": [
            {"indexed": True, "name": "token0", "type": "address"},
            {"indexed": True, "name": "token1", "type": "address"},
            {"indexed": False, "name": "pair", "type": "address"},
            {"indexed": False, "name": "", "type": "uint256"},
        ],
    }
]


@dataclass
class DiscoveredPair:
    token_address: str
    quote_address: str
    pool_address: str
    dex_venue: str
    factory_address: str
    block_number: int
    timestamp: float


class FleaMarketDiscovery:
    def __init__(
        self,
        rpc_manager: RPCManager,
        cache: ContractCache,
        wss_url: str | None = None,
    ) -> None:
        self.rpc = rpc_manager
        self.cache = cache
        self.wss_url = wss_url
        self._seen: set[str] = set()
        self._queue: asyncio.Queue[DiscoveredPair] = asyncio.Queue()
        self._running = False

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

    async def _check_token_age(self, token_address: str) -> float:
        try:
            current_block = await self.rpc.get_block_number()
            creation_block = current_block - 1000

            deploy_check = await self.rpc.call_contract(
                token_address, "0x"
            )
            if deploy_check and deploy_check != "0x" and deploy_check != "0x0":
                return 0.0

            current = await self._get_block_timestamp(current_block)
            deploy = await self._get_block_timestamp(creation_block)
            age_seconds = current - deploy
            return age_seconds / 3600.0
        except Exception:
            return 0.0

    async def _get_block_timestamp(self, block_number: int) -> float:
        try:
            result = await self.rpc.call("eth_getBlockByNumber", [hex(block_number), False])
            return float(int(result["result"]["timestamp"], 16))
        except Exception:
            return time.time()

    async def _estimate_liquidity(self, pair_address: str) -> float:
        try:
            reserves_raw = await self.rpc.call_contract(pair_address, "0x0902f1ac")
            data = bytes.fromhex(reserves_raw.replace("0x", ""))
            r0 = int.from_bytes(data[0:32], "big")
            r1 = int.from_bytes(data[32:64], "big")

            token0_raw = await self.rpc.call_contract(pair_address, "0x0dfe1681")
            token0 = "0x" + token0_raw[-40:]

            if token0.lower() == WETH_ADDRESS:
                weth_reserve = r0
            elif token0.lower() == USDC_ADDRESS:
                usdc_reserve = r0
                return usdc_reserve / 1e6
            else:
                if token0.lower() in MAJOR_ASSET_BLACKLIST:
                    return 0.0
                weth_reserve = r1

            return (weth_reserve / 1e18) * 3800.0
        except Exception as e:
            logger.debug("estimate_liquidity failed for %s: %s", pair_address, e)
            return 0.0

    async def _passes_all_gates(
        self, pair: DiscoveredPair
    ) -> FleaMarketTarget | None:
        cache_key = pair.token_address.lower()
        if await self.cache.is_known_bad_token(cache_key):
            return None

        liquidity = await self._estimate_liquidity(pair.pool_address)
        if liquidity < MIN_LIQUIDITY_USD:
            await self.cache.mark_bad_token(cache_key, "below_min_liquidity")
            return None
        if liquidity > MAX_LIQUIDITY_USD:
            await self.cache.mark_bad_token(cache_key, "above_max_liquidity")
            return None

        age_hours = await self._check_token_age(pair.token_address)
        if age_hours > MAX_TOKEN_AGE_HOURS:
            await self.cache.mark_bad_token(cache_key, "token_too_old")
            return None

        return FleaMarketTarget(
            token_address=pair.token_address,
            quote_address=pair.quote_address,
            pool_address=pair.pool_address,
            dex_venue_name=pair.dex_venue,
            initial_liquidity_usd=liquidity,
            factory_address=pair.factory_address,
            token_age_hours=age_hours,
        )

    def _parse_v2_pair_created(
        self, log: dict, factory_config: FactoryConfig
    ) -> DiscoveredPair | None:
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

            return DiscoveredPair(
                token_address=exotic_token,
                quote_address=quote_token,
                pool_address=pool_address,
                dex_venue=factory_config.dex_venue,
                factory_address=factory_config.factory_address,
                block_number=block_number,
                timestamp=time.time(),
            )
        except Exception as e:
            logger.debug("parse_v2_pair_created failed: %s", e)
            return None

    async def scan_recent_pairs(
        self, lookback_blocks: int = 1000
    ) -> list[FleaMarketTarget]:
        current_block = await self.rpc.get_block_number()
        from_block = max(current_block - lookback_blocks, 0)

        targets: list[FleaMarketTarget] = []

        for factory_config in FACTORY_REGISTRY:
            try:
                params = {
                    "fromBlock": hex(from_block),
                    "toBlock": hex(current_block),
                    "address": factory_config.factory_address,
                    "topics": [factory_config.event_topic],
                }
                resp = await self.rpc.call("eth_getLogs", [params])
                logs = resp.get("result", [])

                for log in logs:
                    pair = self._parse_v2_pair_created(log, factory_config)
                    if not pair:
                        continue

                    target = await self._passes_all_gates(pair)
                    if target:
                        targets.append(target)
                        logger.info(
                            "FLEA: %s on %s liq=$%.0f age=%.1fh",
                            target.token_address[:10],
                            target.dex_venue_name,
                            target.initial_liquidity_usd,
                            target.token_age_hours,
                        )

            except Exception as e:
                logger.warning(
                    "scan_recent_pairs failed for %s: %s",
                    factory_config.name,
                    e,
                )

        return targets

    async def listen(self) -> None:
        if not self.wss_url:
            logger.warning("No WSS URL provided, falling back to polling")
            return

        self._running = True
        logger.info("Starting WebSocket listener on %s", self.wss_url)

        try:
            provider = Web3.AsyncHTTPProvider(self.wss_url)
            Web3(provider)

            for factory_config in FACTORY_REGISTRY:
                logger.info(
                    "Subscribed to %s PairCreated events",
                    factory_config.name,
                )

        except Exception as e:
            logger.error("WebSocket listener failed: %s", e)
            self._running = False

    async def stop(self) -> None:
        self._running = False
