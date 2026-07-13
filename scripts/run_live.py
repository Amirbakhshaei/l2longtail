import asyncio
import csv
import logging
import signal
import time
from pathlib import Path

from config.settings import Settings
from db.blacklist import BlacklistDB
from db.cache import ContractCache
from db.cleared_tokens import ClearedTokensDB
from infra.flea_market_discovery import FleaMarketDiscovery
from infra.keystore import Keystore
from infra.rate_limiter import TokenBucketRateLimiter
from infra.rpc_manager import RPCManager
from monitoring.alerts import TelegramAlerts
from monitoring.logger import setup_logger
from monitoring.metrics import start_metrics_server
from process_a_indexer import ProcessAIndexer
from process_b_sniper import ProcessBSniper

logger = logging.getLogger(__name__)

_shutdown = False
CSV_FILE = Path("paper_results.csv")


def _handle_signal(signum: int, frame: object) -> None:
    global _shutdown
    logger.info("Received signal %d, shutting down...", signum)
    _shutdown = True


def write_results_to_csv(results: list, file_path: Path) -> None:
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
    global _shutdown

    settings = Settings()
    setup_logger(settings.log_level)

    mode = "LIVE" if not settings.dry_run else "PAPER"
    logger.info("Starting Triangular Arbitrage Engine (%s MODE)", mode)
    logger.info("  RPC Primary:  %s", settings.ankr_rpc_url[:40] + "...")
    logger.info("  RPC Fallback: %s", settings.fallback_rpc_url)
    logger.info("  LLM Model:    %s", settings.llm_model_primary)
    logger.info("  Trade Size:   $%.0f", settings.max_trade_size_usd)
    logger.info("  Min Spread:   %.1f%%", settings.min_spread_pct)

    cache = ContractCache(settings.db_path)
    BlacklistDB(settings.db_path)
    await cache.init()

    rate_limiter = TokenBucketRateLimiter(
        rate=settings.rpc_rate_limit_per_sec,
        capacity=settings.rpc_rate_limit_per_sec * 2,
    )
    rpc_manager = RPCManager(
        primary_url=settings.ankr_rpc_url or settings.fallback_rpc_url,
        fallback_url=settings.fallback_rpc_url,
        flashbots_url=settings.flashbots_rpc_url,
        rate_limiter=rate_limiter,
    )

    keystore: Keystore | None = None
    if not settings.dry_run:
        keystore = Keystore(settings.keystore_path, settings.keystore_passphrase)
        logger.info("Keystore loaded, wallet: %s", keystore.address)

    TelegramAlerts(settings.telegram_bot_token, settings.telegram_chat_id)

    start_metrics_server(settings.prometheus_port)
    logger.info("Prometheus metrics server started on port %d", settings.prometheus_port)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cleared_db = ClearedTokensDB()
    flea = FleaMarketDiscovery(rpc_manager, cache)

    process_a = ProcessAIndexer(
        rpc_manager=rpc_manager,
        cleared_db=cleared_db,
        flea_discovery=flea,
    )

    process_b = ProcessBSniper(
        cleared_db=cleared_db,
        rpc_manager=rpc_manager,
        trade_size_usd=settings.max_trade_size_usd,
        gas_usd=settings.gas_baseline_usd,
        min_spread_pct=settings.min_spread_pct,
        dry_run=settings.dry_run,
        llm_api_key=settings.llm_api_key,
        llm_model=settings.llm_model_primary,
    )

    logger.info("Engine ready. Starting Process A (Indexer) + Process B (Sniper)...")

    async def run_process_a() -> None:
        while not _shutdown:
            try:
                await process_a._scan_cycle()
            except Exception as e:
                logger.error("Indexer cycle failed: %s", e)
            await asyncio.sleep(settings.scanner_scan_interval)

    async def run_process_b() -> None:
        while not _shutdown:
            try:
                await process_b._scan_cycle()
            except Exception as e:
                logger.error("Sniper cycle failed: %s", e)
            await asyncio.sleep(1.0)

    start_time = time.monotonic()

    await asyncio.gather(
        run_process_a(),
        run_process_b(),
    )

    elapsed = time.monotonic() - start_time
    results = process_b.get_results()
    executed = [r for r in results if r.status == "EXECUTED"]
    aborted = [r for r in results if r.status == "ABORTED"]

    logger.info(
        "SESSION COMPLETE | duration=%.0fs executed=%d aborted=%d cleared=%d",
        elapsed, len(executed), len(aborted), cleared_db.token_count(),
    )

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
        logger.info("Results written to %s", CSV_FILE)

    await rpc_manager.close()
    logger.info("Engine stopped.")


if __name__ == "__main__":
    asyncio.run(main())
