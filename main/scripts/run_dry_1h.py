import asyncio
import time

from agents.ingestion_node import create_state_from_payload
from agents.state import IngestionPayload
from config.settings import Settings
from db.blacklist import BlacklistDB
from db.cache import ContractCache
from graph.pipeline import compile_graph
from monitoring.logger import setup_logger

PROFILES = [
    IngestionPayload(
        run_id="alpha",
        token_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        liq_usd=50000.0,
        is_verified=True,
        gross_spread_pct=8.5,
        trade_size_usd=200.0,
        pool_reserve_usd=50000.0,
    ),
    IngestionPayload(
        run_id="beta",
        token_address="0x0000000000000000000000000000000000000001",
        pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        liq_usd=50000.0,
        is_verified=True,
        gross_spread_pct=12.0,
        trade_size_usd=200.0,
        pool_reserve_usd=50000.0,
    ),
    IngestionPayload(
        run_id="gamma",
        token_address="0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
        pool_address="0xDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD",
        liq_usd=10000.0,
        is_verified=True,
        gross_spread_pct=15.0,
        trade_size_usd=100.0,
        pool_reserve_usd=10000.0,
    ),
    IngestionPayload(
        run_id="delta",
        token_address="0xEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
        pool_address="0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
        liq_usd=3000.0,
        is_verified=True,
        gross_spread_pct=3.0,
        trade_size_usd=500.0,
        pool_reserve_usd=3000.0,
    ),
    IngestionPayload(
        run_id="unverified",
        token_address="0x1111111111111111111111111111111111111111",
        pool_address="0x2222222222222222222222222222222222222222",
        liq_usd=50000.0,
        is_verified=False,
        gross_spread_pct=10.0,
        trade_size_usd=100.0,
        pool_reserve_usd=50000.0,
    ),
]

PROFILE_NAMES = ["ALPHA", "BETA", "GAMMA", "DELTA", "UNVERIFIED"]
DURATION_SECONDS = 3600


async def main() -> None:
    settings = Settings(dry_run=True)
    setup_logger(settings.log_level)

    print(f"DRY RUN TEST — {DURATION_SECONDS // 3600} hour(s)")
    print(f"  Profiles:    {len(PROFILES)}")
    print(f"  RPC Primary: {settings.alchemy_rpc_url[:40]}...")
    print(f"  LLM:         {settings.llm_model_primary}")
    print(f"  DB:          {settings.db_path}")
    print()

    cache = ContractCache(settings.db_path)
    await cache.init()
    blacklist_db = BlacklistDB(settings.db_path)
    app = await compile_graph(settings, cache, blacklist_db)

    # Pre-seed cache so auditor passes on all profiles
    mock_source = (
        "pragma solidity ^0.8.0; contract Token { "
        "mapping(address => uint256) balances; "
        "function transfer(address to, uint256 amount) public returns (bool) { "
        "balances[msg.sender] -= amount; balances[to] += amount; return true; } }"
    )
    for payload in PROFILES:
        if payload.is_verified:
            await cache.store_source(payload.token_address, True, mock_source, mock_source)
            await cache.store_audit(payload.token_address, True, [], mock_source)
    print("  Cache pre-seeded: all verified profiles pass audit")

    start_time = time.monotonic()
    iteration = 0
    results: dict[str, int] = {name: 0 for name in PROFILE_NAMES}
    errors = 0
    latencies: list[float] = []

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
                    print(f"  [{name}] ERROR: {e}")

                await asyncio.sleep(3)

            print(f"\n  Iteration {iteration} done in {latency:.2f}s")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")

    # Summary
    elapsed_total = time.monotonic() - start_time
    print(f"\n{'=' * 70}")
    print("  DRY RUN SUMMARY")
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
