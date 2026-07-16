"""
Seed the whitelist with REAL, on-chain-verified Uniswap/Camelot v3 pools.

The live pool-discovery APIs (DexScreener / GeckoTerminal) are often
Cloudflare/rate-limited, leaving only the static V2 fallback. This script
instead takes a curated list of candidate v3 pool addresses, verifies each
one directly against the Arbitrum RPC via slot0()/liquidity()/token0()/
token1()/fee(), and merges only the verified pools into config/whitelist.json.

No address is written unless the contract actually responds with live state —
so the engine never trades against a guessed or stale pool.

Usage:
    python scripts/seed_v3_pools.py            # verify + merge verified pools
    python scripts/seed_v3_pools.py --dry-run  # report what would be added, no write
    python scripts/seed_v3_pools.py --force    # re-verify and overwrite existing v3 entries
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings  # noqa: E402
from infra.rpc_manager import RPCManager  # noqa: E402
from infra.rate_limiter import TokenBucketRateLimiter  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("seed_v3_pools")

V3_SLOT0 = "0x3859248c"        # slot0()
V3_LIQUIDITY = "0x1a686502"    # liquidity()
V3_TOKEN0 = "0x0dfe1681"       # token0()
V3_TOKEN1 = "0x0dcd1bf9"       # token1()
V3_FEE = "0xddca3f43"          # fee()

# Curated candidate pools (Uniswap v3 on Arbitrum One). These addresses are
# checked on-chain; only responding contracts are added. Token/fee come from
# the contract itself, never from this list.
CANDIDATE_V3_POOLS = [
    # Uniswap v3, Arbitrum
    "0xc6962004f452be9203591991d15f6b388e09e8d0",  # WETH/USDC 0.05%
    "0x4e68ccd3e89ef504cbe578fadb91d402f4bd7497",  # WETH/USDC 0.30%
    "0x641832b9a28be11c5c9d3d0f6b8e6b8f0e6b9c5a",  # WETH/USDT 0.05%
    # Camelot v3, Arbitrum
    "0x1f221111af0840e6ac3656065d90b4df7a5f1a9f",  # Camelot v3 (verify live)
]


async def _call(rpc: RPCManager, to: str, selector: str) -> str | None:
    try:
        raw = await rpc.call_contract(to, selector)
    except Exception as e:  # noqa: BLE001
        logger.debug("call %s on %s failed: %s", selector, to[:10], e)
        return None
    if not raw or raw == "0x":
        return None
    return raw


def _parse_slot0(raw: str) -> tuple[int, int]:
    b = bytes.fromhex(raw[2:])
    sqrt_price_x96 = int.from_bytes(b[0:32], "big")
    tick = int.from_bytes(b[32:64], "big")
    if tick >= 2**255:
        tick -= 2**256
    return sqrt_price_x96, tick


async def verify_pool(rpc: RPCManager, pool: str) -> dict | None:
    pool = Web3_to_checksum(pool)
    slot0_raw = await _call(rpc, pool, V3_SLOT0)
    if not slot0_raw:
        return None
    liq_raw = await _call(rpc, pool, V3_LIQUIDITY)
    t0_raw = await _call(rpc, pool, V3_TOKEN0)
    t1_raw = await _call(rpc, pool, V3_TOKEN1)
    fee_raw = await _call(rpc, pool, V3_FEE)
    if not (liq_raw and t0_raw and t1_raw and fee_raw):
        return None

    sqrt_price_x96, tick = _parse_slot0(slot0_raw)
    liquidity = int.from_bytes(bytes.fromhex(liq_raw[2:]), "big")
    # address-typed returns are right-aligned in a 32-byte word: take last 20.
    token0 = "0x" + bytes.fromhex(t0_raw[2:])[-20:].hex()
    token1 = "0x" + bytes.fromhex(t1_raw[2:])[-20:].hex()
    fee = int(fee_raw, 16)

    if liquidity == 0 or sqrt_price_x96 == 0:
        return None

    dex = "camelot_v3" if pool.lower().startswith("0x1f2211") else "uniswap_v3"
    return {
        "pair_address": pool.lower(),
        "token0": token0.lower(),
        "token1": token1.lower(),
        "dex": dex,
        "fee_tier": fee,
        # store initial state so the engine has a seed before first Swap log
        "_seed_sqrt_price_x96": sqrt_price_x96,
        "_seed_tick": tick,
        "_seed_liquidity": liquidity,
    }


def Web3_to_checksum(addr: str) -> str:
    from eth_utils import to_checksum_address

    return to_checksum_address(addr)


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Seed verified v3 pools")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be added without writing.")
    parser.add_argument("--force", action="store_true",
                        help="Re-verify and overwrite existing v3 entries.")
    args = parser.parse_args()

    settings = Settings()
    rl = TokenBucketRateLimiter(
        rate=settings.rpc_rate_limit_per_sec, capacity=settings.rpc_rate_limit_per_sec * 2
    )
    rpc = RPCManager(
        settings.ankr_rpc_url or settings.fallback_rpc_url,
        settings.fallback_rpc_url,
        settings.flashbots_rpc_url,
        rl,
    )

    whitelist_path = Path(settings.whitelist_path)
    existing = json.loads(whitelist_path.read_text()) if whitelist_path.exists() else []
    by_addr = {e["pair_address"].lower(): e for e in existing}

    verified: list[dict] = []
    for cand in CANDIDATE_V3_POOLS:
        pool = cand.lower()
        if pool in by_addr and not args.force:
            logger.info("already present: %s — skipping", pool[:12])
            continue
        entry = await verify_pool(rpc, pool)
        if entry is None:
            logger.warning("not a live v3 pool (rejected): %s", pool[:12])
            continue
        logger.info(
            "verified v3: %s fee=%d liq=%d", pool[:12], entry["fee_tier"], entry["_seed_liquidity"]
        )
        verified.append(entry)

    if not verified:
        logger.info("No new verified v3 pools to add.")
        return

    # Build the merged list: keep non-v3, add/replace v3. Seed state is only
    # used to initialise the engine graph; strip it before writing.
    v3_addrs = {v["pair_address"].lower() for v in verified}
    kept = [
        e for e in existing
        if e.get("dex") not in ("uniswap_v3", "camelot_v3")
        or (args.force and e["pair_address"].lower() in v3_addrs)
    ]
    # remove old v3 entries that we are replacing
    kept = [e for e in kept if not (e.get("dex") in ("uniswap_v3", "camelot_v3")
                                   and e["pair_address"].lower() in v3_addrs)]
    for v in verified:
        kept.append({
            "pair_address": v["pair_address"],
            "token0": v["token0"],
            "token1": v["token1"],
            "dex": v["dex"],
            "fee_tier": v["fee_tier"],
        })

    if args.dry_run:
        logger.info("[dry-run] would write %d total pools (%d new v3):",
                    len(kept), len(verified))
        for v in verified:
            logger.info("   + %s %s/%s fee=%d", v["dex"], v["token0"][:8],
                        v["token1"][:8], v["fee_tier"])
        return

    whitelist_path.write_text(json.dumps(kept, indent=2))
    logger.info("Wrote %d pools (%d verified v3 added) to %s",
                len(kept), len(verified), whitelist_path)


if __name__ == "__main__":
    asyncio.run(main())
