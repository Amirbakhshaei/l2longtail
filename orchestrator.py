"""
Main Orchestrator: Runs Process A (Indexer) and Process B (Sniper) together.

Process A runs in the background building the whitelist (no LLM).
Process B runs in the foreground executing triangular arbitrage.
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import signal
import time
from pathlib import Path

from dotenv import load_dotenv

from db.cache import ContractCache
from db.cleared_tokens import ClearedTokensDB
from infra.flea_market_discovery import FleaMarketDiscovery
from infra.rate_limiter import TokenBucketRateLimiter
from infra.rpc_manager import RPCManager
from process_a_indexer import ProcessAIndexer
from process_b_sniper import ProcessBSniper

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

CSV_FILE = Path("paper_results.csv")

_shutdown = False


def _handle_signal(signum: int, frame: object) -> None:
    global _shutdown
    logger.info("Received signal %d, shutting down...", signum)
    _shutdown = True


def write_results_to_csv(results: list[dict], file_path: Path) -> None:
    if not results:
        return

    fieldnames = [
        "trade_id", "token_address", "dex_name",
        "weth_to_exotic_pool", "exotic_to_quote_pool", "quote_to_weth_pool",
        "gross_spread_pct", "trade_size_usd",
        "gas_overhead_usd", "net_profit_usd", "status", "abort_reason",
    ]

    write_header = not file_path.exists() or file_path.stat().st_size == 0

    with open(file_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for result in results:
            writer.writerow(result)


async def main() -> None:
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    duration = int(os.getenv("SCAN_DURATION", "300"))
    trade_size = float(os.getenv("TRADE_SIZE_USD", "10"))
    min_spread = float(os.getenv("MIN_SPREAD_PCT", "0.5"))

    ankr_key = os.getenv("ANKR_API_KEY", "")
    primary_url = (
        f"https://rpc.ankr.com/arbitrum/{ankr_key}"
        if ankr_key
        else "https://rpc.ankr.com/arbitrum"
    )
    fallback_url = "https://arb1.arbitrum.io/rpc"

    rate_limiter = TokenBucketRateLimiter(rate=10, capacity=5)
    rpc = RPCManager(primary_url, fallback_url, "", rate_limiter)
    cleared_db = ClearedTokensDB()

    cache = ContractCache("longtail.db")
    await cache.init()
    flea = FleaMarketDiscovery(rpc, cache)

    llm_key = os.getenv("LLM_API_KEY", "")
    llm_model = os.getenv("LLM_MODEL_PRIMARY", "llama-3.3-70b-versatile")

    print("\nTRIANGULAR ARBITRAGE SYSTEM")
    print(f"  Mode:        {'PAPER' if dry_run else 'LIVE'}")
    print(f"  Duration:    {duration}s")
    print(f"  Trade size:  ${trade_size:.0f}")
    print(f"  Min spread:  {min_spread:.1f}%")
    print(f"  CSV output:  {CSV_FILE}")
    print()

    process_a = ProcessAIndexer(
        rpc_manager=rpc,
        cleared_db=cleared_db,
        flea_discovery=flea,
    )

    process_b = ProcessBSniper(
        cleared_db=cleared_db,
        rpc_manager=rpc,
        trade_size_usd=trade_size,
        gas_usd=0.02,
        min_spread_pct=min_spread,
        dry_run=dry_run,
        llm_api_key=llm_key,
        llm_model=llm_model,
    )

    start_time = time.monotonic()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    async def run_process_a():
        while not _shutdown:
            try:
                await process_a._scan_cycle()
            except Exception as e:
                logger.error("Indexer cycle failed: %s", e)
            await asyncio.sleep(30.0)

    async def run_process_b():
        while not _shutdown:
            try:
                await process_b._scan_cycle()
            except Exception as e:
                logger.error("Sniper cycle failed: %s", e)
            await asyncio.sleep(1.0)

    print("Starting Process A (Indexer) and Process B (Sniper)...")
    print()

    await asyncio.gather(
        run_process_a(),
        run_process_b(),
    )

    results = process_b.get_results()
    executed = [r for r in results if r.status == "EXECUTED"]
    aborted = [r for r in results if r.status == "ABORTED"]

    print(f"\n{'=' * 70}")
    print("  SESSION COMPLETE")
    print(f"{'=' * 70}")
    print(f"  Duration:       {time.monotonic() - start_time:.0f}s")
    print(f"  Trades executed: {len(executed)}")
    print(f"  Trades aborted:  {len(aborted)}")
    print(f"  Cleared tokens:  {cleared_db.token_count()}")
    print()

    if results:
        csv_results = []
        for r in results:
            csv_results.append({
                "trade_id": r.trade_id,
                "token_address": r.token_address,
                "dex_name": r.dex_name,
                "weth_to_exotic_pool": r.weth_to_exotic_pool,
                "exotic_to_quote_pool": r.exotic_to_quote_pool,
                "quote_to_weth_pool": r.quote_to_weth_pool,
                "gross_spread_pct": f"{r.gross_spread_pct:.2f}",
                "trade_size_usd": f"{r.trade_size_usd:.2f}",
                "gas_overhead_usd": f"{r.gas_overhead_usd:.4f}",
                "net_profit_usd": f"{r.net_profit_usd:.4f}",
                "status": r.status,
                "abort_reason": r.abort_reason,
            })
        write_results_to_csv(csv_results, CSV_FILE)
        print(f"  Results written to {CSV_FILE}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
