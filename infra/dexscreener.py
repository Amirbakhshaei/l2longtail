from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.geckoterminal.com/api/v2"
ARBITRUM_NETWORK = "arbitrum"


@dataclass
class DexPair:
    chain_id: str
    dex_id: str
    pair_address: str
    base_token_address: str
    base_token_symbol: str
    quote_token_address: str | None
    quote_token_symbol: str | None
    price_usd: float | None
    liquidity_usd: float | None
    volume_24h: float | None
    pair_created_at: int | None


class DexScreenerClient:
    def __init__(self, rate_limit_seconds: float = 1.0) -> None:
        self._client: httpx.AsyncClient | None = None
        self._rate_limit_seconds = rate_limit_seconds
        self._last_request_time: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"User-Agent": "LongTailBot/1.0"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _rate_limit_wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_limit_seconds:
            await __import__("asyncio").sleep(self._rate_limit_seconds - elapsed)
        self._last_request_time = time.monotonic()

    async def _get(self, path: str) -> dict | list:
        await self._rate_limit_wait()
        client = await self._get_client()
        url = f"{BASE_URL}{path}"
        for attempt in range(3):
            try:
                resp = await client.get(url)
                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning("GeckoTerminal 429, waiting %ds", wait)
                    await __import__("asyncio").sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning("GeckoTerminal 429, waiting %ds", wait)
                    await __import__("asyncio").sleep(wait)
                    continue
                logger.error("GeckoTerminal HTTP %d: %s", e.response.status_code, url)
                return []
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.error("GeckoTerminal connection error: %s", e)
                return []
        return []

    async def search_pairs(self, query: str) -> list[DexPair]:
        data = await self._get(
            f"/networks/{ARBITRUM_NETWORK}/pools?page=1&sort=h24_volume_usd_desc"
        )
        return self._parse_pools(data)

    async def get_pairs_by_token(self, token_address: str) -> list[DexPair]:
        data = await self._get(
            f"/networks/{ARBITRUM_NETWORK}/tokens/{token_address}/pools"
        )
        return self._parse_pools(data)

    async def get_pair(self, pair_address: str) -> list[DexPair]:
        data = await self._get(
            f"/networks/{ARBITRUM_NETWORK}/pools/{pair_address}"
        )
        return self._parse_pools(data)

    async def get_trending_tokens(self) -> list[DexPair]:
        data = await self._get(
            f"/networks/{ARBITRUM_NETWORK}/trending_pools?page=1"
        )
        return self._parse_pools(data)

    async def get_new_pools(self) -> list[DexPair]:
        data = await self._get(
            f"/networks/{ARBITRUM_NETWORK}/new_pools?page=1"
        )
        return self._parse_pools(data)

    async def discover_long_tail_tokens(
        self,
        min_liq_usd: float = 500.0,
        max_liq_usd: float = 2500.0,
    ) -> list[DexPair]:
        all_pairs: list[DexPair] = []

        trending = await self.get_trending_tokens()
        all_pairs.extend(trending)

        new_pools = await self.get_new_pools()
        all_pairs.extend(new_pools)

        seen_tokens: set[str] = set()
        filtered: list[DexPair] = []
        for pair in all_pairs:
            if pair.base_token_address in seen_tokens:
                continue
            seen_tokens.add(pair.base_token_address)

            if pair.liquidity_usd is None:
                continue
            if not (min_liq_usd <= pair.liquidity_usd <= max_liq_usd):
                continue
            if pair.quote_token_symbol is None:
                continue

            filtered.append(pair)

        logger.info(
            "GeckoTerminal discovery: %d total pairs -> %d filtered (liq $%.0f-$%.0f)",
            len(all_pairs),
            len(filtered),
            min_liq_usd,
            max_liq_usd,
        )
        return filtered

    def _parse_pools(self, data: dict | list) -> list[DexPair]:
        if isinstance(data, dict):
            items = data.get("data", [])
        elif isinstance(data, list):
            items = data
        else:
            return []

        pairs: list[DexPair] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            attrs = item.get("attributes", {})
            relationships = item.get("relationships", {})

            dex_info = relationships.get("dex", {}).get("data", {})
            dex_name = dex_info.get("id", "").replace("_arbitrum", "").replace("-arbitrum", "")
            pair_addr = attrs.get("address", "")

            if not pair_addr or len(pair_addr) != 42 or not pair_addr.startswith("0x"):
                continue

            base_token_rel = relationships.get("base_token", {})
            base_token = base_token_rel.get("data", {})
            base_id = base_token.get("id", "")
            base_addr = base_id.split("_")[-1] if "_" in base_id else base_id

            if not base_addr or len(base_addr) != 42 or not base_addr.startswith("0x"):
                continue

            name = attrs.get("name", "")
            parts = name.split(" / ") if name else []
            base_symbol = parts[0] if parts else ""
            quote_symbol = parts[1] if len(parts) > 1 else ""

            quote_token_rel = relationships.get("quote_token", {})
            quote_token = quote_token_rel.get("data", {})
            quote_id = quote_token.get("id", "")
            quote_addr = quote_id.split("_")[-1] if "_" in quote_id else quote_id

            price_usd = float(attrs.get("base_token_price_usd", 0) or 0)
            volume_24h = float(attrs.get("volume_usd", {}).get("h24", 0) or 0)

            pool_created = attrs.get("pool_created_at")
            pair_created = None
            if pool_created:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(pool_created.replace("Z", "+00:00"))
                    pair_created = int(dt.timestamp())
                except (ValueError, TypeError):
                    pass

            pairs.append(
                DexPair(
                    chain_id=ARBITRUM_NETWORK,
                    dex_id=dex_name,
                    pair_address=pair_addr,
                    base_token_address=base_addr,
                    base_token_symbol=base_symbol,
                    quote_token_address=quote_addr,
                    quote_token_symbol=quote_symbol,
                    price_usd=price_usd if price_usd > 0 else None,
                    liquidity_usd=float(attrs.get("reserve_in_usd", 0) or 0),
                    volume_24h=volume_24h if volume_24h > 0 else None,
                    pair_created_at=pair_created,
                )
            )
        return pairs
