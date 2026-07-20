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

# DexScreener per-token pairs (authoritative, supports our core legs and
# returns feeBps + liquidity + volume). Used as a primary source and as the
# backfill when GeckoTerminal rate-limits before reaching TARGET_POOLS.
DEXSCREENER_TOKEN_PAIRS = "https://api.dexscreener.com/token-pairs/v1/arbitrum/{token}"

TARGET_POOLS = int(os.getenv("TARGET_POOLS", "500"))
# 30 pages * 20 = 600 candidate pools. We then keep the top TARGET_POOLS by
# 24h volume, comfortably reaching the 400-500 long-tail topology.
PAGES = int(os.getenv("SEED_PAGES", "30"))
PAGE_SLEEP = 2.0                                     # stay under 30 req/min (~12/min)
POOLS_PER_PAGE = 20

# Liquidity floor lowered to $1,500: the previous $5k gate was too strict
# and starved the long-tail surface area. $1.5k still filters untradeable
# dust that would bleed to slippage under flashloan-sized notional, while
# permitting the engine to ingest low-cap meme / long-tail pairs that route
# through a reliable asset.
MIN_LIQUIDITY_USD = 1_500.0
# Volume gate kept deliberately low: it only screens out truly dead pools.
# The liquidity floor is the real slippage guard per the directives.
MIN_VOLUME_24H_USD = 1_000.0

# Relaxed asset gate (Change 2): the strict CORE_TOKENS whitelist is
# removed. Instead we require that *at least one* leg of every ingested pool
# is a reliable routing asset — WETH, USDC, USDT, or ARB. The other leg may
# be any long-tail / meme token (provided it clears the liquidity floor), so
# the topology expands into the long tail without ever routing through a
# fully illiquid pair.
ROUTING_ASSETS = {
    "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",  # WETH
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # USDC
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # USDT
    "0x912ce59144191c1204e64559fe8253a0e49e6548",  # ARB
}
# V2 *and* V3 venues are ingested. The engine now quotes V3 routes flawlessly
# on-chain via QuoterV2 + Multicall3, so concentrated-liquidity pools are
# first-class edges in the graph. The `fee` tier is strictly required to encode
# the V3 bytes path, so it is always extracted and saved. We ingest all major
# blue-chip Arbitrum venues so the core-pair topology can be widened toward the
# target pool count without touching unverified long-tail dust.
ALLOWED_DEXES = {
    "uniswap_v2", "sushiswap", "camelot_v2",
    "uniswap_v3", "camelot_v3",
    "pancakeswap", "ramses", "trader_joe", "camelot", "pancake_v3",
    "zyberswap", "arbswap", "swapr", "chronos", "solidlizard", "spartadex",
}
ALLOWED_CHAINS = {"arbitrum"}

# Map GeckoTerminal / DexScreener dexId -> engine router key
# (config.constants.DEX_ROUTERS). Both V2 and V3 variants are mapped so the
# engine can route either topology; `fee_tier` is recorded for every V3 pool.
DEX_NORMALIZE = {
    "uniswap": "uniswap_v2",
    "uniswap-v2": "uniswap_v2",
    "uniswap-v3": "uniswap_v3",
    "uniswap_v3": "uniswap_v3",
    "uniswap_v2": "uniswap_v2",
    "sushiswap": "sushiswap",
    "sushi": "sushiswap",
    "sushiswap_v2": "sushiswap",
    "camelot": "camelot_v2",
    "camelot-v2": "camelot_v2",
    "camelot-v3": "camelot_v3",
    "camelot_v3": "camelot_v3",
    "camelot_v2": "camelot_v2",
    "pancakeswap": "pancakeswap",
    "pancake": "pancakeswap",
    "pancake_v3": "pancake_v3",
    "ramses": "ramses",
    "traderjoe": "trader_joe",
    "trader_joe": "trader_joe",
    "zyberswap": "zyberswap",
    "arbswap": "arbswap",
    "swapr": "swapr",
    "chronos": "chronos",
    "solidlizard": "solidlizard",
    "spartadex": "spartadex",
}

# Default fee tier (bps) per V3 dex when the source does not report one.
# Any normalized dex suffixed `_v3` is treated as a concentrated-liquidity
# venue and therefore requires a `fee_tier` to encode the V3 bytes path.
DEFAULT_V3_FEE_BPS = {
    "uniswap_v3": 3000, "camelot_v3": 3000,
    "pancake_v3": 2500, "ramses": 3000, "zyberswap": 3000,
    "arbswap": 3000, "swapr": 3000, "chronos": 3000,
    "solidlizard": 3000, "spartadex": 3000, "trader_joe": 3000,
}

WHITELIST_PATH = Path("config/whitelist.json")

# Curated, proven Arbitrum pools — last-resort seed if both live APIs are
# rate-limited / blocklisted. V3 entries carry their fee tier so the on-chain
# quoter can encode the bytes path. Only used when nothing else is available.
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
    {"pair_address": "0xc6962004f452be9203591991d15f6b388e09e8d0",
     "token0": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
     "token1": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
     "dex": "uniswap_v3", "fee_tier": 500},
]


def _valid_pair(p: dict) -> bool:
    if p.get("chainId") not in ALLOWED_CHAINS:
        return False
    dex_id = p.get("dexId", "")
    # Map any variant to the canonical engine key and require it to be an
    # allowed venue (V2 or V3). Unmapped ids resolve to nothing and fail.
    norm = DEX_NORMALIZE.get(dex_id, dex_id)
    if norm not in ALLOWED_DEXES:
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
    # Relaxed long-tail gate: at least one leg must be a reliable routing
    # asset (WETH/USDC/USDT/ARB). The other leg may be any long-tail or
    # meme token, provided it clears the liquidity floor. This widens the
    # topology toward 400-500 pools without ever routing a fully illiquid pair.
    base_l = (base or "").lower()
    quote_l = (quote or "").lower()
    if not (base_l in ROUTING_ASSETS or quote_l in ROUTING_ASSETS):
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
    # V3 pools require a `fee_tier` (bps) to encode the V3 bytes path in the
    # on-chain quoter. Extract it from the source (DexScreener may report it
    # as a fraction, e.g. 0.003) and default per-dex when missing. Any dex
    # marked V3 (in DEFAULT_V3_FEE_BPS) is treated as concentrated liquidity.
    if dex in DEFAULT_V3_FEE_BPS:
        raw = p.get("feeBps") or p.get("fee")
        if not raw:
            fee = DEFAULT_V3_FEE_BPS[dex]
        elif isinstance(raw, float) and raw < 1:
            # DexScreener may report the fee as a fraction (e.g. 0.003 = 0.3%).
            fee = int(raw * 10000)
        else:
            fee = int(raw)
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
    # GeckoTerminal exposes the fee tier under `fee` (string, e.g. "0.003"
    # or already in bps). Carried through so V3 pools get a `feeBps`.
    fee = attrs.get("fee")
    common: dict = {
        "chainId": "arbitrum",
        "dexId": dex_id,
        "pairAddress": pair_addr,
        "baseToken": {"address": base_addr},
        "quoteToken": {"address": quote_addr},
        "liquidity": {"usd": liq},
        "volume": {"h24": vol},
    }
    if fee is not None:
        try:
            f = float(fee)
            common["feeBps"] = int(f * 10000) if f < 1 else int(f)
        except (TypeError, ValueError):
            pass
    return common


async def _fetch_gecko_pools(client: httpx.AsyncClient) -> list[dict]:
    """Paginate the top-volume Arbitrum pools. One request per page, 2.0s
    apart. On a 429 we back off and retry the same page (GeckoTerminal's
    ceiling is ~30 req/min, so a few seconds' sleep clears it) rather than
    aborting — this lets us pull far deeper than a single burst allows.
    Returns Gecko-native pool dicts (pre _valid_pair)."""
    common: list[dict] = []
    for page in range(1, PAGES + 1):
        url = f"{GECKO_NETWORK_POOLS}?page={page}"
        resp = None
        for attempt in range(5):
            try:
                resp = await client.get(url)
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.warning("GeckoTerminal connection error on page %d: %s", page, e)
                await asyncio.sleep(PAGE_SLEEP)
                continue
            if resp.status_code == 429:
                logger.warning("GeckoTerminal 429 on page %d (attempt %d) — backing off", page, attempt)
                await asyncio.sleep(5.0)
                continue
            break
        if resp is None:
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
# DexScreener — per-core-token pairs (primary + backfill toward TARGET_POOLS)
# --------------------------------------------------------------------------
async def _fetch_dexscreener_core(client: httpx.AsyncClient) -> list[dict]:
    """Pull every DexScreener pair that touches one of our ROUTING_ASSETS on
    Arbitrum. This is the authoritative source and the backfill when
    GeckoTerminal 429s before reaching TARGET_POOLS. Rate-limit friendly:
    one request per routing asset (4 total), retried on 429 with a short sleep.
    Pairs returned may have a long-tail/meme other leg (allowed under the
    relaxed asset gate) — _valid_pair enforces the single-routing-leg rule.
    """
    out: list[dict] = []
    for token in sorted(ROUTING_ASSETS):
        url = DEXSCREENER_TOKEN_PAIRS.format(token=token)
        for attempt in range(3):
            try:
                resp = await client.get(url)
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.warning("DexScreener connection error (token %s): %s", token, e)
                break
            if resp.status_code == 429:
                logger.warning("DexScreener 429 (token %s) — backing off", token)
                await asyncio.sleep(2.0)
                continue
            if resp.status_code >= 400:
                logger.warning("DexScreener HTTP %d (token %s)", resp.status_code, token)
                break
            data = resp.json()
            if isinstance(data, list):
                for p in data:
                    out.append({
                        "chainId": (p.get("chainId") or "arbitrum").lower(),
                        "dexId": p.get("dexId", ""),
                        "pairAddress": p.get("pairAddress", ""),
                        "baseToken": {"address": (p.get("baseToken") or {}).get("address", "")},
                        "quoteToken": {"address": (p.get("quoteToken") or {}).get("address", "")},
                        "liquidity": {"usd": (p.get("liquidity") or {}).get("usd", 0) or 0},
                        "volume": {"h24": (p.get("volume") or {}).get("h24", 0) or 0},
                        "feeBps": p.get("feeBps", p.get("fee")),
                    })
            break
        await asyncio.sleep(0.5)
    logger.info("DexScreener core-token pull: %d raw pairs", len(out))
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
        # GeckoTerminal top-down bulk crawl (authoritative volume ranking).
        gecko_pairs = await _fetch_gecko_pools(client)
        # DexScreener per-core-token pull — primary backfill that reliably
        # reaches deep into the core-pair topology when Gecko 429s. Merged
        # (dedup by pair address) so we maximize the validated surface area.
        ds_pairs = await _fetch_dexscreener_core(client)

        pairs = gecko_pairs + ds_pairs

        # Last-resort: if absolutely nothing came back live, seed from the
        # curated static core set so the engine still has a topology.
        if not pairs:
            logger.warning("Both live sources empty — using static core fallback set")
            pairs = STATIC_FALLBACK[:]

        for p in pairs:
            if not _valid_pair(p):
                continue
            pair_addr = p["pairAddress"].lower()
            if pair_addr in pools:
                continue
            vol = (p.get("volume") or {}).get("h24") or 0.0
            pools[pair_addr] = {"fmt": _format(p), "vol": vol}

    live = sorted(pools.values(), key=lambda d: d["vol"], reverse=True)[:TARGET_POOLS]

    # Concurrent V3/V2 topology: overwrite config/whitelist.json with only
    # this run's validated core-pair pools (both V2 and V3). A merge of a
    # prior file could re-introduce stale non-core pools, so the prior file
    # is deliberately not carried forward.
    final = [p["fmt"] for p in live]

    # Hard floor: only if the live crawl found nothing (rate-limited / down),
    # fall back to the curated core set so the engine always has a topology.
    if not final:
        logger.warning("No live pools available — using static fallback set")
        final = STATIC_FALLBACK[:TARGET_POOLS]

    WHITELIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WHITELIST_PATH.write_text(json.dumps(final, indent=2) + "\n")

    n_v3 = sum(1 for p in final if "fee_tier" in p)
    n_v2 = len(final) - n_v3
    logger.info(
        "Wrote %d long-tail pools to %s (live=%d, discovered=%d, V3=%d, V2=%d)",
        len(final),
        WHITELIST_PATH,
        len(live),
        len(pools),
        n_v3,
        n_v2,
    )


if __name__ == "__main__":
    asyncio.run(main())
