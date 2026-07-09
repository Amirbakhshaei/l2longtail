from __future__ import annotations

import logging
from dataclasses import dataclass

from config.constants import WETH_ADDRESS
from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

GET_RESERVES_SELECTOR = "0x0902f1ac"
TOKEN0_SELECTOR = "0x0dfe1681"
TOKEN1_SELECTOR = "0xd21220a7"
DECIMALS_SELECTOR = "0x313ce567"

WETH_DECIMALS = 18


@dataclass
class PoolReserves:
    pair_address: str
    token0: str
    token1: str
    reserve0: int
    reserve1: int
    token0_decimals: int
    token1_decimals: int
    weth_balance: int
    quote_balance: int


class PoolFetcher:
    def __init__(self, rpc_manager: RPCManager) -> None:
        self.rpc = rpc_manager
        self._cache: dict[str, PoolReserves] = {}

    async def _call(self, to: str, data: str) -> bytes | None:
        raw = await self.rpc.call_contract(to, data)
        if not raw or raw == "0x":
            return None
        return bytes.fromhex(raw[2:])

    async def fetch_pool_reserves(self, pair_address: str) -> PoolReserves | None:
        if pair_address in self._cache:
            return self._cache[pair_address]

        try:
            token0_raw = await self._call(pair_address, TOKEN0_SELECTOR)
            token1_raw = await self._call(pair_address, TOKEN1_SELECTOR)
            reserves_raw = await self._call(pair_address, GET_RESERVES_SELECTOR)

            if not token0_raw or not token1_raw or not reserves_raw:
                logger.warning("Failed to fetch pool data for %s", pair_address)
                return None

            token0 = "0x" + token0_raw[-20:].hex()
            token1 = "0x" + token1_raw[-20:].hex()

            reserve0 = int.from_bytes(reserves_raw[:32], "big")
            reserve1 = int.from_bytes(reserves_raw[32:64], "big")

            token0_decimals = 18
            token1_decimals = 18

            try:
                dec0_raw = await self._call(token0, DECIMALS_SELECTOR)
                if dec0_raw:
                    token0_decimals = int.from_bytes(dec0_raw, "big")
            except Exception:
                pass

            try:
                dec1_raw = await self._call(token1, DECIMALS_SELECTOR)
                if dec1_raw:
                    token1_decimals = int.from_bytes(dec1_raw, "big")
            except Exception:
                pass

            weth_addr = WETH_ADDRESS.lower()
            if token0.lower() == weth_addr:
                weth_balance = reserve0
                quote_balance = reserve1
            elif token1.lower() == weth_addr:
                weth_balance = reserve1
                quote_balance = reserve0
            else:
                weth_balance = 0
                quote_balance = 0

            pool = PoolReserves(
                pair_address=pair_address,
                token0=token0,
                token1=token1,
                reserve0=reserve0,
                reserve1=reserve1,
                token0_decimals=token0_decimals,
                token1_decimals=token1_decimals,
                weth_balance=weth_balance,
                quote_balance=quote_balance,
            )
            self._cache[pair_address] = pool
            return pool

        except Exception as e:
            logger.error("Failed to fetch reserves for %s: %s", pair_address, e)
            return None

    async def fetch_multiple_pools(
        self, pair_addresses: list[str]
    ) -> dict[str, PoolReserves]:
        results: dict[str, PoolReserves] = {}
        for addr in pair_addresses:
            pool = await self.fetch_pool_reserves(addr)
            if pool:
                results[addr] = pool
        return results

    def compute_pool_reserve_usd(
        self, pool: PoolReserves, weth_price_usd: float
    ) -> float:
        weth_in_pool = pool.weth_balance / (10 ** WETH_DECIMALS)
        return weth_in_pool * weth_price_usd

    def clear_cache(self) -> None:
        self._cache.clear()
