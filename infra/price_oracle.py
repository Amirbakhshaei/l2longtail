from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

WETH_USDC_POOL = "0xf64dfe17c8b87f012fcf50fbda1d62bfa148366a"
GECKOTERMINAL_URL = (
    "https://api.geckoterminal.com/api/v2/networks/arbitrum/tokens"
    "/0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
)
WETH_DECIMALS = 18
USDC_DECIMALS = 6
CACHE_TTL = 60.0


class WETHPriceOracle:
    """Multi-tiered WETH price oracle: GeckoTerminal REST → on-chain pool reserves."""

    def __init__(self, rpc_manager: object | None = None) -> None:
        self.rpc = rpc_manager
        self._cached_price: float | None = None
        self._cache_ttl: float = 0.0

    async def get_weth_price(self) -> float:
        now = time.time()
        if self._cached_price is not None and now - self._cache_ttl < CACHE_TTL:
            return self._cached_price

        price = await self._tier1_geckoterminal()
        if price is not None and price > 0:
            self._cached_price = price
            self._cache_ttl = now
            return price

        price = await self._tier2_on_chain()
        if price is not None and price > 0:
            self._cached_price = price
            self._cache_ttl = now
            return price

        raise ValueError(
            "All WETH pricing oracle tiers exhausted. Engine is completely blind."
        )

    async def _tier1_geckoterminal(self) -> float | None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(GECKOTERMINAL_URL)
                if response.status_code == 200:
                    data = response.json()
                    price_usd = float(data["data"]["attributes"]["price_usd"])
                    if price_usd > 0:
                        logger.info("WETH oracle Tier 1 (GeckoTerminal): $%.2f", price_usd)
                        return price_usd
        except Exception as e:
            logger.warning("Tier 1 off-chain WETH oracle failed: %s", e)
        return None

    async def _tier2_on_chain(self) -> float | None:
        if self.rpc is None:
            return None
        try:
            raw = await self.rpc.call_contract(WETH_USDC_POOL, "0x0902f1ac")
            data = bytes.fromhex(raw.replace("0x", ""))
            r0 = int.from_bytes(data[0:32], "big")
            r1 = int.from_bytes(data[32:64], "big")

            token0_raw = await self.rpc.call_contract(WETH_USDC_POOL, "0x0dfe1681")
            token0 = "0x" + token0_raw[-40:]

            weth_addr = "0x82af49447d8a07e3bd95bd0d56f35241523fab1"
            if token0.lower() == weth_addr:
                weth_reserve = r0
                usdc_reserve = r1
            else:
                weth_reserve = r1
                usdc_reserve = r0

            if weth_reserve > 0 and usdc_reserve > 0:
                price = (usdc_reserve / 10**USDC_DECIMALS) / (
                    weth_reserve / 10**WETH_DECIMALS
                )
                logger.info("WETH oracle Tier 2 (on-chain): $%.2f", price)
                return price
        except Exception as e:
            logger.warning("Tier 2 on-chain WETH pool fallback failed: %s", e)
        return None
