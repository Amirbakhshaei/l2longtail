"""
scripts/seed_whitelist.py

Asynchronously discovers the most active micro-cap liquidity pools on
Arbitrum, filters them by DEX / liquidity / 24h-volume, and overwrites
config/whitelist.json with the top 500.

Primary source: DexScreener (`/latest/dex/token-pairs/v1/arbitrum/<tokens>`).
Fallback source: GeckoTerminal (the in-repo client) — used automatically if
DexScreener is unreachable / blocklisted (e.g. Cloudflare 403).

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

# DexScreener
BASE_URL = "https://api.dexscreener.com/latest/dex"
TOKEN_PAIRS_URL = f"{BASE_URL}/token-pairs/v1/arbitrum"

# GeckoTerminal fallback
GECKO_URL = "https://api.geckoterminal.com/api/v2"

TARGET_POOLS = int(os.getenv("TARGET_POOLS", "500"))
MAX_TOKENS_VISITED = int(os.getenv("MAX_TOKENS_VISITED", "400"))
BATCH_SIZE = 30
MIN_LIQUIDITY_USD = 10_000.0
MIN_VOLUME_24H_USD = 5_000.0
ALLOWED_DEXES = {"uniswap", "sushiswap", "camelot", "uniswap_v3", "camelot_v3"}
ALLOWED_CHAINS = {"arbitrum"}

# Map DexScreener/Gecko dexId -> engine router key (config.constants.DEX_ROUTERS).
# V3 venues carry a fee tier which is recorded separately in the whitelist.
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
    # GeckoTerminal ids
    "uniswap_v2": "uniswap_v2",
    "sushi": "sushiswap",
    "sushiswap_v2": "sushiswap",
}

# Default fee tier (bps) per V3 dex when the source does not report one.
DEFAULT_V3_FEE_BPS = {"uniswap_v3": 3000, "camelot_v3": 3000}

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
    # Extra V2-native hubs to widen the V2 discovery surface.
    "0x912CE59144191C1204E64559FE8253a0e49E6548",  # ARB (dup-safe)
    "0x2C1dA6a06f5a8E43aFebB8c15e8d1e66893B5e",  # GRAIL
    "0xB31f63e2442BCA96bd58a2D7CCd5fCd3c37A2e",  # USDC.e
    "0x17FC77aA2C394C36d52B6Cd1F3Ea98e4aE9d84f",  # AAVE
    "0x2f7240E4f10129b2b83a8a8CD9ADa3bF6C9f3",  # USDC.e alt
]

# Curated, proven Arbitrum V2 pools — last-resort seed if the live API
# is rate-limited / blocklisted (e.g. DexScreener behind Cloudflare).
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
# DexScreener
# --------------------------------------------------------------------------
async def _fetch_dexscreener(
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
            if resp.status_code >= 400:
                logger.error("DexScreener HTTP %d", resp.status_code)
                return []
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
        except json.JSONDecodeError as e:
            logger.error("DexScreener bad JSON: %s", e)
            return []
    return []


# --------------------------------------------------------------------------
# GeckoTerminal fallback
# --------------------------------------------------------------------------
def _gecko_pair_to_common(pool: dict) -> dict | None:
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


async def _fetch_gecko(
    client: httpx.AsyncClient, tokens: list[str]
) -> list[dict]:
    out: list[dict] = []
    for idx, tok in enumerate(tokens):
        # Honor GeckoTerminal's public rate limit (~1 req/0.6s).
        if idx > 0:
            await asyncio.sleep(1.2)
        got = False
        for attempt in range(4):
            try:
                url = f"{GECKO_URL}/networks/arbitrum/tokens/{tok}/pools?page=1"
                resp = await client.get(url)
                if resp.status_code == 429:
                    # Bounded, capped backoff so a throttled IP can't stall the
                    # whole crawl for minutes.
                    wait = min(2 ** (attempt + 1), 6)
                    logger.warning("GeckoTerminal 429, waiting %ds", wait)
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code >= 400:
                    break
                items = resp.json().get("data", [])
                for pool in items:
                    common = _gecko_pair_to_common(pool)
                    if common:
                        out.append(common)
                got = True
                break
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.debug("GeckoTerminal token %s error: %s", tok[:10], e)
                await asyncio.sleep(min(2 ** (attempt + 1), 6))
                continue
            except Exception as e:  # noqa: BLE001
                logger.debug("GeckoTerminal token %s error: %s", tok[:10], e)
                break
        if not got:
            logger.warning("GeckoTerminal skipped token %s (rate-limited)", tok[:10])
        # Early-exit once we have enough qualifying pools.
        if sum(1 for p in out if _valid_pair(p)) >= TARGET_POOLS:
            break
    return out


# --------------------------------------------------------------------------
# Crawler
# --------------------------------------------------------------------------
async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    frontier: set[str] = {t.lower() for t in SEED_TOKENS}
    visited: set[str] = set()
    pools: dict[str, dict] = {}
    early_exit = False

    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": "LongTailBot/1.0"}
    ) as client:
        while frontier and len(visited) < MAX_TOKENS_VISITED:
            batch = [frontier.pop() for _ in range(min(BATCH_SIZE, len(frontier)))]
            visited.update(batch)

            # GeckoTerminal is the reliable source on Arbitrum (DexScreener is
            # frequently behind Cloudflare 403). Prefer Gecko; only fall back to
            # DexScreener when Gecko yields nothing.
            pairs = await _fetch_gecko(client, batch)
            if not pairs:
                logger.warning(
                    "GeckoTerminal returned nothing for batch; trying DexScreener"
                )
                pairs = await _fetch_dexscreener(client, batch)

            if pairs:
                early_exit = False
            elif not early_exit:
                early_exit = True
            else:
                # Two consecutive empty batches after reaching rate-limit calm-down:
                # pause once, then continue crawling remaining seeds.
                logger.warning("Empty batch — cooling down 5s")
                await asyncio.sleep(5.0)

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
    live = [d["fmt"] for d in ranked[:TARGET_POOLS]]

    # Load any existing whitelist and merge so a rate-limited / partial crawl
    # never wipes previously-discovered pools. Live pools take precedence.
    existing: dict[str, dict] = {}
    if WHITELIST_PATH.exists():
        try:
            for p in json.loads(WHITELIST_PATH.read_text()):
                existing[p["pair_address"].lower()] = p
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Existing whitelist unreadable — starting fresh")

    merged = {p["pair_address"].lower(): p for p in live}
    merged.update(existing)  # keep any prior pools not rediscovered this run

    # Rank merged pools by discovered volume (existing pools default to 0 so
    # freshly-discovered high-volume pools float to the top).
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
        "Wrote %d pools to %s (live=%d, prior=%d, discovered=%d, tokens=%d)",
        len(final),
        WHITELIST_PATH,
        len(live),
        len(existing),
        len(pools),
        len(visited),
    )


if __name__ == "__main__":
    asyncio.run(main())
