"""
scripts/seed_whitelist.py

Top-down discovery of the highest-volume liquidity pools on Arbitrum via
GeckoTerminal's network-pools pagination endpoint, then writes the top
TARGET_POOLS pools to config/whitelist.json.

Why top-down?
    The previous crawler walked every token address individually
    (GET /networks/arbitrum/tokens/<tok>/pools). That blows through
    GeckoTerminal's 30 req/min ceiling instantly and triggers 429s. The
    network-pools endpoint returns 20 pools per page in bulk, so 25 pages
    (500 pools) costs only 25 requests. At a strict 2.0s sleep between pages
    we use ~12 req/min — comfortably under the limit, on localhost or VPS.

Endpoint:
    GET https://api.geckoterminal.com/api/v2/networks/arbitrum/pools?page={n}

Fallback:
    If GeckoTerminal 429s or errors, fall back to the merge of an existing
    whitelist (if present) and the curated STATIC_FALLBACK set so the engine
    always has something to route. The script never crashes.

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

# GeckoTerminal network-pools (top-down, bulk pagination).
GECKO_URL = "https://api.geckoterminal.com/api/v2"
GECKO_NETWORK_POOLS = f"{GECKO_URL}/networks/arbitrum/pools"

# DexScreener top-pools fallback (network-wide trending).
DEXSCREENER_TOP = "https://api.dexscreener.com/latest/dex/pairs/arbitrum"

TARGET_POOLS = int(os.getenv("TARGET_POOLS", "500"))
PAGES = int(os.getenv("SEED_PAGES", "25"))          # 25 pages * 20 = 500 pools
PAGE_SLEEP = 2.0                                     # stay under 30 req/min (~12/min)
POOLS_PER_PAGE = 20

MIN_LIQUIDITY_USD = 10_000.0
MIN_VOLUME_24H_USD = 5_000.0
ALLOWED_DEXES = {"uniswap", "sushiswap", "camelot", "uniswap_v3", "camelot_v3"}
ALLOWED_CHAINS = {"arbitrum"}

# Map GeckoTerminal / DexScreener dexId -> engine router key
# (config.constants.DEX_ROUTERS). V3 venues carry a fee tier recorded separately.
DEX_NORMALIZE = {
    "uniswap": "uniswap_v2",
    "uniswap-v2": "uniswap_v2",
    "uniswap-v3": "uniswap_v3",
    "uniswap_v3": "uniswap_v3",
    "sushiswap": "sushiswap",
    "camelot": "camelot_v2",
    "camelot-v2": "camelot_v2",
    "camelot-v3": "camelot_v3",
    "camelot_v3": "camelot_v3",
    "uniswap_v2": "uniswap_v2",
    "sushi": "sushiswap",
    "sushiswap_v2": "sushiswap",
}

# Default fee tier (bps) per V3 dex when the source does not report one.
DEFAULT_V3_FEE_BPS = {"uniswap_v3": 3000, "camelot_v3": 3000}

WHITELIST_PATH = Path("config/whitelist.json")

# Curated, proven Arbitrum pools — last-resort seed if both live APIs are
# rate-limited / blocklisted. Only used when nothing else is available.
STATIC_FALLBACK = [
    {"pair_address": "0xf64dfe17c8b87f012fcf50fbda1d62bfa148366a",
     "token0": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
     "token1": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
     "dex": "uniswap_v2"},
    {"pair_address": "0x57b85fef094e10b5eecdf350af688299e9553378",
     "token0": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
     "token1": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
     "dex": "sushiswap"},
    {"pair_address": "0x3797927e7fc20f13f46b295f8889ea8050cb5e21",
     "token0": "0x6efa9b8883dfb78fd75cd89d8474c44c3cbda469",
     "token1": "0xfd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
     "dex": "uniswap_v2"},
    {"pair_address": "0xaffbe59f6dd93f0bb129f0cfb94d84c2fb87f39f",
     "token0": "0x4749881d148d91f63b69abf6a67fed139b233ca6",
     "token1": "0xfd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
     "dex": "uniswap_v2"},
    # Uniswap v3 WETH/USDC 0.05% (Arbitrum) — high-liquidity V3 hub.
    # NOTE: pool address must be verified on-chain; the engine validates
    # sqrtPriceX96/tick/liquidity via the V3 indexer before routing.
    {"pair_address": "0xc6962004f452be9203591991d15f6b388e09e8d0",
     "token0": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
     "token1": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
     "dex": "uniswap_v3", "fee_tier": 500},
]


def _valid_pair(p: dict) -> bool:
    if p.get("chainId") not in ALLOWED_CHAINS:
        return False
    dex_id = p.get("dexId", "")
    # Route V2 *and* V3 pools; map any variant to the canonical engine key
    # and require that key to be an allowed venue.
    if DEX_NORMALIZE.get(dex_id, dex_id) not in ALLOWED_DEXES:
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
    dex = DEX_NORMALIZE.get(p["dexId"], p["dexId"])
    entry = {
        "pair_address": p["pairAddress"].lower(),
        "token0": p["baseToken"]["address"].lower(),
        "token1": p["quoteToken"]["address"].lower(),
        "dex": dex,
    }
    # V3 pools carry a fee tier (bps); default it if not reported.
    if dex in DEFAULT_V3_FEE_BPS:
        fee = p.get("feeBps") or p.get("fee")
        fee = int(fee) if fee else DEFAULT_V3_FEE_BPS[dex]
        # DexScreener sometimes reports fee as a fraction (e.g. 0.003).
        if isinstance(fee, float) and fee < 1:
            fee = int(fee * 10000)
        entry["fee_tier"] = int(fee)
    return entry


# --------------------------------------------------------------------------
# GeckoTerminal — network pools (top-down bulk pagination)
# --------------------------------------------------------------------------
def _gecko_pool_to_common(pool: dict) -> dict | None:
    """GeckoTerminal network-pools payload uses the same data envelope as the
    token-pools endpoint, so reuse the same parser shape."""
    attrs = pool.get("attributes", {})
    rel = pool.get("relationships", {})
    base = rel.get("base_token", {}).get("data", {})
    quote = rel.get("quote_token", {}).get("data", {})
    dex = rel.get("dex", {}).get("data", {}).get("id", "").replace("_arbitrum", "")
    base_addr = base.get("id", "").split("_")[-1]
    quote_addr = quote.get("id", "").split("_")[-1]
    pair_addr = attrs.get("address", "")
    if not (base_addr and quote_addr and pair_addr):
        return None
    vol = float((attrs.get("volume_usd") or {}).get("h24", 0) or 0)
    liq = float(attrs.get("reserve_in_usd", 0) or 0)
    # Gecko ids look like "uniswap_v3_arbitrum" -> strip the chain suffix.
    dex_id = dex.split("_arbitrum")[0] if "_arbitrum" in dex else dex
    return {
        "chainId": "arbitrum",
        "dexId": dex_id,
        "pairAddress": pair_addr,
        "baseToken": {"address": base_addr},
        "quoteToken": {"address": quote_addr},
        "liquidity": {"usd": liq},
        "volume": {"h24": vol},
    }


async def _fetch_gecko_pools(client: httpx.AsyncClient) -> list[dict]:
    """Paginate the top-volume Arbitrum pools. One request per page, 2.0s
    apart. Returns Gecko-native pool dicts (pre _valid_pair)."""
    common: list[dict] = []
    for page in range(1, PAGES + 1):
        url = f"{GECKO_NETWORK_POOLS}?page={page}"
        try:
            resp = await client.get(url)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning("GeckoTerminal connection error on page %d: %s", page, e)
            break
        if resp.status_code == 429:
            logger.warning("GeckoTerminal 429 on page %d — stopping crawl", page)
            break
        if resp.status_code >= 400:
            logger.warning("GeckoTerminal HTTP %d on page %d — stopping", resp.status_code, page)
            break
        items = resp.json().get("data", [])
        if not items:
            break
        for pool in items:
            c = _gecko_pool_to_common(pool)
            if c:
                common.append(c)
        logger.info("GeckoTerminal page %d: %d pools (%d total)", page, len(items), len(common))
        if page < PAGES:
            await asyncio.sleep(PAGE_SLEEP)
    return common


# --------------------------------------------------------------------------
# DexScreener — top-pools fallback (network-wide trending)
# --------------------------------------------------------------------------
async def _fetch_dexscreener_top(client: httpx.AsyncClient) -> list[dict]:
    try:
        resp = await client.get(DEXSCREENER_TOP)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("DexScreener connection error: %s", e)
        return []
    if resp.status_code >= 400:
        logger.warning("DexScreener HTTP %d", resp.status_code)
        return []
    data = resp.json()
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for p in data:
        out.append({
            "chainId": (p.get("chainId") or "arbitrum").lower(),
            "dexId": p.get("dexId", ""),
            "pairAddress": p.get("pairAddress", ""),
            "baseToken": {"address": (p.get("baseToken") or {}).get("address", "")},
            "quoteToken": {"address": (p.get("quoteToken") or {}).get("address", "")},
            "liquidity": {"usd": (p.get("liquidity") or {}).get("usd", 0) or 0},
            "volume": {"h24": (p.get("volume") or {}).get("h24", 0) or 0},
        })
    return out


# --------------------------------------------------------------------------
# Crawler
# --------------------------------------------------------------------------
async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    pools: dict[str, dict] = {}

    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": "LongTailBot/1.0"}
    ) as client:
        pairs = await _fetch_gecko_pools(client)

        # Fallback chain: Gecko failed/empty -> DexScreener top -> static.
        if not pairs:
            logger.warning("GeckoTerminal yielded nothing — trying DexScreener top pools")
            pairs = await _fetch_dexscreener_top(client)

        for p in pairs:
            if not _valid_pair(p):
                continue
            pair_addr = p["pairAddress"].lower()
            if pair_addr in pools:
                continue
            vol = (p.get("volume") or {}).get("h24") or 0.0
            pools[pair_addr] = {"fmt": _format(p), "vol": vol}

    live = sorted(pools.values(), key=lambda d: d["vol"], reverse=True)[:TARGET_POOLS]

    # Load any existing whitelist and merge so a rate-limited / partial crawl
    # never wipes previously-discovered pools. Live pools take precedence.
    existing: dict[str, dict] = {}
    if WHITELIST_PATH.exists():
        try:
            for p in json.loads(WHITELIST_PATH.read_text()):
                existing[p["pair_address"].lower()] = p
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Existing whitelist unreadable — starting fresh")

    merged = {p["fmt"]["pair_address"].lower(): p["fmt"] for p in live}
    merged.update(existing)  # keep any prior pools not rediscovered this run

    def _vol(pair: str) -> float:
        for d in pools.values():
            if d["fmt"]["pair_address"].lower() == pair:
                return d["vol"]
        return 0.0

    final = sorted(merged.values(), key=_vol, reverse=True)[:TARGET_POOLS]

    # Hard floor: only if the file is genuinely empty (no live, no prior).
    if not final:
        logger.warning("No pools available — using static fallback set")
        final = STATIC_FALLBACK[:TARGET_POOLS]

    WHITELIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WHITELIST_PATH.write_text(json.dumps(final, indent=2) + "\n")

    logger.info(
        "Wrote %d pools to %s (live=%d, prior=%d, discovered=%d)",
        len(final),
        WHITELIST_PATH,
        len(live),
        len(existing),
        len(pools),
    )


if __name__ == "__main__":
    asyncio.run(main())
