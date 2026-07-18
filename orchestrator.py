"""
Main Orchestrator: Runs Process A (Sync Engine) and Process B (Sniper) together.

Process A monitors whitelisted pools for Sync events.
Process B evaluates triangular arbitrage on updated pools.
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

from db.cleared_tokens import ClearedTokensDB
from infra.flea_market_discovery import FleaMarketDiscovery
from infra.rate_limiter import TokenBucketRateLimiter
from infra.rpc_manager import RPCManager
from infra.websocket_listener import WebSocketListener, LogsPoller
from monitoring.alerts import TelegramAlerts
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
    lookback = int(os.getenv("SYNC_LOOKBACK_BLOCKS", "50"))

    ankr_key = (os.getenv("ANKR_API_KEY", "") or "").strip()
    primary_url = (
        f"https://rpc.ankr.com/arbitrum/{ankr_key}"
        if ankr_key
        else "https://rpc.ankr.com/arbitrum"
    )
    fallback_url = "https://arb1.arbitrum.io/rpc"
    from config.settings import Settings

    settings = Settings()
    wss_url = settings.wss_rpc_url or (
        "wss://"
        + settings.fallback_rpc_url.replace("https://", "", 1)
        .replace("http://", "", 1)
        .split("/")[0]
        + "/ws"
    )
    vault_address = os.getenv(
        "BALANCER_VAULT_ADDRESS", "0xBA12222222228d8Ba445958a75a0704d566BF2C8"
    )
    executor_address = os.getenv("FLASHLOAN_EXECUTOR_ADDRESS", "")

    rate_limiter = TokenBucketRateLimiter(rate=10, capacity=5)
    rpc = RPCManager(
        primary_url, fallback_url, "", rate_limiter,
        execution_rpcs=settings.execution_rpcs_list,
    )
    cleared_db = ClearedTokensDB()

    whitelist_path = os.getenv("WHITELIST_PATH", "config/whitelist.json")
    flea = FleaMarketDiscovery(rpc, whitelist_path=whitelist_path)

    llm_key = os.getenv("LLM_API_KEY", "")
    llm_model = os.getenv("LLM_MODEL_PRIMARY", "llama-3.3-70b-versatile")

    tg = TelegramAlerts(
        os.getenv("TELEGRAM_BOT_TOKEN", ""),
        os.getenv("TELEGRAM_CHAT_ID", ""),
    )

    sync_queue: asyncio.Queue = asyncio.Queue()

    use_wss = bool(
        os.getenv("WSS_RPC_URL")
        and settings.sync_transport in ("auto", "wss")
        and str(os.getenv("WSS_RPC_URL")).startswith(("ws://", "wss://"))
    )
    if use_wss:
        sync_source = WebSocketListener(
            wss_url, flea.whitelisted_addresses, v3_addresses=flea.v3_addresses
        )
        sync_transport = "wss"
    else:
        if settings.sync_transport == "wss":
            print("  [warn] sync_transport=wss but no valid WSS_RPC_URL; using HTTP polling")
        sync_source = LogsPoller(
            rpc_manager=rpc,
            whitelisted_addresses=flea.whitelisted_addresses,
            v3_addresses=flea.v3_addresses,
            poll_interval=settings.sync_poll_interval,
            poll_blocks=settings.sync_poll_blocks,
        )
        sync_transport = "http"

    print("\nGRAPH ARBITRAGE SYSTEM (SYNC ENGINE)")
    print(f"  Mode:        {'PAPER' if dry_run else 'LIVE'}")
    print(f"  Duration:    {duration}s")
    print(f"  Pools:       {flea.pool_count}")
    print(f"  Quoting:     QuoterV2 + Multicall3 (on-chain grid search)")
    print(f"  Transport:   {sync_transport}")
    print(f"  Vault:       {vault_address}")
    print(f"  Executor:    {executor_address or 'NOT DEPLOYED'}")
    print(f"  CSV output:  {CSV_FILE}")
    print()

    process_a = ProcessAIndexer(
        rpc_manager=rpc,
        cleared_db=cleared_db,
        websocket_listener=sync_source,
        sync_queue=sync_queue,
        flea_discovery=flea,
        transport=sync_transport,
    )

    process_b = ProcessBSniper(
        cleared_db=cleared_db,
        rpc_manager=rpc,
        dry_run=dry_run,
        llm_api_key=llm_key,
        llm_model=llm_model,
        on_opportunity=tg.notify_opportunity,
        sync_queue=sync_queue,
        vault_address=vault_address,
        executor_address=executor_address,
        graph_mode=True,
    )

    start_time = time.monotonic()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    await tg.notify_engine_start(
        "PAPER" if dry_run else "LIVE", flea.pool_count
    )

    async def flush_notifications():
        while not _shutdown:
            try:
                results = process_b.get_results()
                for r in results:
                    if r.status == "EXECUTED":
                        await tg.notify_trade_executed(
                            token_address=r.token_address,
                            buy_dex=r.dex_name,
                            sell_dex=r.dex_name,
                            spread_pct=r.gross_spread_pct,
                            net_profit=r.net_profit_usd,
                            mode="PAPER" if dry_run else "LIVE",
                        )
                    elif r.status == "ABORTED":
                        await tg.notify_trade_aborted(
                            token_address=r.token_address,
                            spread_pct=r.gross_spread_pct,
                            reason=r.abort_reason,
                        )
                results.clear()
            except Exception as e:
                logger.error("Notification flush failed: %s", e)
            await asyncio.sleep(1.0)

    print("Starting Process A (WSS Sync) and Process B (Graph Sniper)...")
    print()

    await asyncio.gather(
        process_a.run(),
        process_b.run(),
        flush_notifications(),
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
    print(f"  Sync events:     {process_a._events_processed}")
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
