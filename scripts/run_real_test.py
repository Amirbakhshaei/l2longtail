import asyncio
import csv
import time

from agents.ingestion_node import create_state_from_payload
from agents.state import IngestionPayload
from config.settings import Settings
from db.blacklist import BlacklistDB
from db.cache import ContractCache
from graph.pipeline import compile_graph
from infra.source_fetcher import fetch_contract_source
from monitoring.logger import setup_logger

# Real Arbitrum One tokens with verified contracts on Arbiscan
PROFILES = [
    IngestionPayload(
        run_id="weth",
        token_address="0x82aF49447D8A07e3bd95bD0d56f35241523fBab1",  # WETH
        pool_address="0xB3d2D8E032fD75D61fAe7B8f25868174D576Bb3e",  # Fake pool
        liq_usd=500000.0,
        is_verified=True,
        gross_spread_pct=2.0,
        trade_size_usd=200.0,
        pool_reserve_usd=500000.0,
    ),
    IngestionPayload(
        run_id="usdc",
        token_address="0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
        pool_address="0xB3d2D8E032fD75D61fAe7B8f25868174D576Bb3e",  # Fake pool
        liq_usd=1000000.0,
        is_verified=True,
        gross_spread_pct=1.5,
        trade_size_usd=500.0,
        pool_reserve_usd=1000000.0,
    ),
    IngestionPayload(
        run_id="arb",
        token_address="0x912CE59144191C1204E64559FE8253a0e49E6548",  # ARB
        pool_address="0xB3d2D8E032fD75D61fAe7B8f25868174D576Bb3e",  # Fake pool
        liq_usd=200000.0,
        is_verified=True,
        gross_spread_pct=5.0,
        trade_size_usd=100.0,
        pool_reserve_usd=200000.0,
    ),
]

PROFILE_NAMES = ["WETH", "USDC", "ARB"]
DURATION_SECONDS = 300  # 5 minutes for quick test
CSV_FILE = "results.csv"


async def main() -> None:
    settings = Settings(dry_run=True)
    setup_logger(settings.log_level)

    print(f"REAL DATA TEST — {DURATION_SECONDS}s")
    print(f"  Profiles:    {len(PROFILES)}")
    print(f"  LLM:         {settings.llm_model_primary}")
    print(f"  Arbiscan:    {'configured' if settings.arbiscan_api_key else 'MISSING'}")
    print(f"  CSV output:  {CSV_FILE}")
    print()

    if not settings.arbiscan_api_key or settings.arbiscan_api_key == "YOUR_ARBISCAN_API_KEY_HERE":
        print("ERROR: Set ARBISCAN_API_KEY in .env first")
        return

    cache = ContractCache(settings.db_path)
    blacklist_db = BlacklistDB(settings.db_path)

    # Create source fetcher
    class SourceFetcherImpl:
        async def fetch_source(self, token_address: str) -> str:
            return await fetch_contract_source(token_address, settings.arbiscan_api_key)

    source_fetcher = SourceFetcherImpl()
    app = await compile_graph(settings, cache, blacklist_db, source_fetcher=source_fetcher)

    start_time = time.monotonic()
    iteration = 0
    results: dict[str, int] = {name: 0 for name in PROFILE_NAMES}
    errors = 0
    latencies: list[float] = []
    csv_rows: list[dict] = []

    try:
        while time.monotonic() - start_time < DURATION_SECONDS:
            iteration += 1
            elapsed = time.monotonic() - start_time
            remaining = DURATION_SECONDS - elapsed

            print(f"\n{'=' * 70}")
            print(f"  ITER {iteration}  |  Elapsed: {elapsed:.0f}s  |  Remaining: {remaining:.0f}s")
            print(f"{'=' * 70}")

            for i, (name, payload) in enumerate(zip(PROFILE_NAMES, PROFILES)):
                if time.monotonic() - start_time >= DURATION_SECONDS:
                    break

                run_id = f"{name.lower()}-{iteration:04d}"
                state = create_state_from_payload(
                    IngestionPayload(
                        run_id=run_id,
                        token_address=payload.token_address,
                        pool_address=payload.pool_address,
                        liq_usd=payload.liq_usd,
                        is_verified=payload.is_verified,
                        gross_spread_pct=payload.gross_spread_pct,
                        trade_size_usd=payload.trade_size_usd,
                        pool_reserve_usd=payload.pool_reserve_usd,
                    ),
                    dry_run=True,
                )

                t0 = time.monotonic()
                try:
                    result = await app.ainvoke(
                        state.model_dump(),
                        config={"configurable": {"thread_id": run_id}},
                    )
                    latency = time.monotonic() - t0
                    latencies.append(latency)

                    status = result["status"]
                    row = {
                        "iteration": iteration,
                        "token": name,
                        "run_id": run_id,
                        "token_address": payload.token_address,
                        "liq_usd": payload.liq_usd,
                        "trade_size_usd": payload.trade_size_usd,
                        "gross_spread_pct": payload.gross_spread_pct,
                        "status": status,
                        "slippage_pct": result.get("expected_slippage_pct", ""),
                        "net_profit_usd": result.get("net_profit_usd", ""),
                        "tx_hash": result.get("tx_hash", ""),
                        "reason": result.get("reason", ""),
                        "latency_ms": round(latency * 1000, 1),
                    }
                    csv_rows.append(row)

                    if status == "ABORTED":
                        results[name] += 1
                        print(
                            f"  [{name}] ABORTED | {result.get('reason', 'N/A')}"
                        )
                    elif status == "EXECUTED":
                        results[name] += 1
                        print(
                            f"  [{name}] EXECUTED | profit=${result.get('net_profit_usd', 'N/A')} "
                            f"| slippage={result.get('expected_slippage_pct', 'N/A')}% "
                            f"| tx={result.get('tx_hash', 'N/A')[:16]}..."
                        )
                    else:
                        results[name] += 1
                        print(f"  [{name}] {status}")

                except Exception as e:
                    latency = time.monotonic() - t0
                    latencies.append(latency)
                    errors += 1
                    csv_rows.append({
                        "iteration": iteration,
                        "token": name,
                        "run_id": run_id,
                        "token_address": payload.token_address,
                        "liq_usd": payload.liq_usd,
                        "trade_size_usd": payload.trade_size_usd,
                        "gross_spread_pct": payload.gross_spread_pct,
                        "status": "ERROR",
                        "slippage_pct": "",
                        "net_profit_usd": "",
                        "tx_hash": "",
                        "reason": str(e),
                        "latency_ms": round(latency * 1000, 1),
                    })
                    print(f"  [{name}] ERROR: {e}")

                await asyncio.sleep(5)

            print(f"\n  Iteration {iteration} done in {latency:.2f}s")

            # Write CSV after each iteration (survives timeout/kill)
            with open(CSV_FILE, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
                writer.writeheader()
                writer.writerows(csv_rows)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")

    # Write CSV
    with open(CSV_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f"\n  Results saved to {CSV_FILE}")

    # Summary
    elapsed_total = time.monotonic() - start_time
    print(f"\n{'=' * 70}")
    print("  REAL DATA TEST SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Duration:        {elapsed_total:.0f}s ({elapsed_total / 3600:.1f}h)")
    print(f"  Iterations:      {iteration}")
    print(f"  Profiles/run:    {len(PROFILES)}")
    print(f"  Total runs:      {sum(results.values())}")
    print(f"  Errors:          {errors}")
    print(f"  Avg latency:     {sum(latencies) / len(latencies):.3f}s" if latencies else "")
    print(f"  Max latency:     {max(latencies):.3f}s" if latencies else "")
    print("\n  Per-profile results:")
    for name in PROFILE_NAMES:
        print(f"    {name:12s}: {results[name]}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
