"""
scripts/seed_whitelist.py

Asynchronously queries the DexScreener API to discover the most active
micro-cap liquidity pools on Arbitrum, filters them by DEX / liquidity /
24h-volume, and overwrites config/whitelist.json with the top 500.

Run with:
    python -m scripts.seed_whitelist
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com/latest/dex"
TOKEN_PAIRS_URL = f"{BASE_URL}/token-pairs/v1/arbitrum"

TARGET_POOLS = 500
MAX_TOKENS_VISITED = 400
BATCH_SIZE = 30
MIN_LIQUIDITY_USD = 10_000.0
MIN_VOLUME_24H_USD = 5_000.0
ALLOWED_DEXES = {"uniswap", "sushiswap", "camelot"}
ALLOWED_CHAINS = {"arbitrum"}

# Map DexScreener dexId -> engine router key (config.constants.DEX_ROUTERS).
DEX_NORMALIZE = {
    "uniswap": "uniswap_v2",
    "uniswap-v2": "uniswap_v2",
    "uniswap-v3": "uniswap_v2",
    "sushiswap": "sushiswap",
    "camelot": "camelot_v2",
    "camelot-v2": "camelot_v2",
    "camelot-v3": "camelot_v2",
}

WHITELIST_PATH = Path("config/whitelist.json")

# Initial Arbitrum blue-chip seeds; the crawler BFS-expands from these.
SEED_TOKENS = [
    "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
    "0xfd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",  # USDT
    "0x912CE59144191C1204E64559FE8253a0e49E6548",  # ARB
    "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f",  # WBTC
    "0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a",  # GMX
    "0x539bdE0d7Dbd336b79148AA742883198BBF60342",  # MAGIC
    "0xDA10009cBd5d07dd0CeCc66161FC93D7c9000Da1",  # DAI
    "0xf97f4df75117a78c1a5a0dbb814af92458539fb4",  # LINK
    "0xfa9fa403952bf6964d4469a7ebbe16ac158aed17",  # UNI
]


def _valid_pair(p: dict) -> bool:
    if p.get("chainId") not in ALLOWED_CHAINS:
        return False
    if p.get("dexId") not in ALLOWED_DEXES:
        return False
    liq = (p.get("liquidity") or {}).get("usd") or 0.0
    vol = (p.get("volume") or {}).get("h24") or 0.0
    if liq < MIN_LIQUIDITY_USD:
        return False
    if vol < MIN_VOLUME_24H_USD:
        return False
    base = (p.get("baseToken") or {}).get("address")
    quote = (p.get("quoteToken") or {}).get("address")
    pair = p.get("pairAddress")
    if not (base and quote and pair):
        return False
    if len(pair) != 42 or not pair.startswith("0x"):
        return False
    return True


def _format(p: dict) -> dict:
    return {
        "pair_address": p["pairAddress"].lower(),
        "token0": p["baseToken"]["address"].lower(),
        "token1": p["quoteToken"]["address"].lower(),
        "dex": DEX_NORMALIZE.get(p["dexId"], p["dexId"]),
    }


async def _fetch_batch(
    client: httpx.AsyncClient, tokens: list[str]
) -> list[dict]:
    url = f"{TOKEN_PAIRS_URL}/{','.join(tokens)}"
    for attempt in range(4):
        try:
            resp = await client.get(url)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("DexScreener 429, waiting %ds", wait)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                await asyncio.sleep(2 ** (attempt + 1))
                continue
            logger.error("DexScreener HTTP %d", e.response.status_code)
            return []
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error("DexScreener connection error: %s", e)
            return []
    return []


async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    frontier: set[str] = {t.lower() for t in SEED_TOKENS}
    visited: set[str] = set()
    pools: dict[str, dict] = {}

    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": "LongTailBot/1.0"}
    ) as client:
        while frontier and len(visited) < MAX_TOKENS_VISITED:
            batch = [frontier.pop() for _ in range(min(BATCH_SIZE, len(frontier)))]
            visited.update(batch)

            pairs = await _fetch_batch(client, batch)
            logger.info(
                "Fetched %d pairs for %d tokens (visited=%d, pools=%d)",
                len(pairs),
                len(batch),
                len(visited),
                len(pools),
            )

            for p in pairs:
                if not _valid_pair(p):
                    continue
                pair_addr = p["pairAddress"].lower()
                if pair_addr in pools:
                    continue
                vol = (p.get("volume") or {}).get("h24") or 0.0
                pools[pair_addr] = {"fmt": _format(p), "vol": vol}
                # Expand frontier with counterpart tokens of qualifying pools.
                for tok in (
                    p["baseToken"]["address"].lower(),
                    p["quoteToken"]["address"].lower(),
                ):
                    if tok not in visited:
                        frontier.add(tok)

            if len(pools) >= TARGET_POOLS * 3:
                break
            await asyncio.sleep(0.25)

    ranked = sorted(pools.values(), key=lambda d: d["vol"], reverse=True)
    final = [d["fmt"] for d in ranked[:TARGET_POOLS]]

    WHITELIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WHITELIST_PATH.write_text(json.dumps(final, indent=2) + "\n")

    logger.info(
        "Wrote %d pools to %s (from %d discovered, %d tokens visited)",
        len(final),
        WHITELIST_PATH,
        len(pools),
        len(visited),
    )


if __name__ == "__main__":
    asyncio.run(main())
