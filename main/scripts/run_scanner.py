from __future__ import annotations

import asyncio
import csv
import time

from agents.ingestion_node import create_state_from_payload
from agents.state import IngestionPayload
from config.constants import DEX_ROUTERS, WETH_ADDRESS
from config.settings import Settings
from db.blacklist import BlacklistDB
from db.cache import ContractCache
from graph.pipeline import compile_graph
from infra.dexscreener import DexScreenerClient
from infra.flea_market_discovery import FleaMarketDiscovery
from infra.pair_finder import PairFinder
from infra.pool_fetcher import PoolFetcher
from infra.price_oracle import PriceOracle, RouterQuote
from infra.rate_limiter import TokenBucketRateLimiter
from infra.rpc_manager import RPCManager
from infra.source_fetcher import fetch_contract_source
from infra.token_discovery import TokenDiscovery
from monitoring.logger import setup_logger

CSV_FILE = "paper_results.csv"
DURATION_SECONDS = 300


async def _scan_flea_market(
    discovery: FleaMarketDiscovery,
    oracle: PriceOracle,
    pair_finder: PairFinder,
    pool_fetcher: PoolFetcher,
    settings: Settings,
    source_fetcher: object,
    start_time: float,
    iteration: int,
) -> list[tuple[str, IngestionPayload, str, str, float]]:
    targets = await discovery.scan_recent_pairs(lookback_blocks=50000)
    if not targets:
        return []

    opportunities = []
    for target in targets:
        if time.monotonic() - start_time >= DURATION_SECONDS:
            break

        token_addr = target.token_address
        token_symbol = token_addr[:8]
        quote_token = target.quote_address

        try:
            known_dexes = []
            for dex_name in DEX_ROUTERS:
                finder_result = await pair_finder.find_pair(
                    dex_name, token_addr, quote_token
                )
                if finder_result:
                    known_dexes.append(dex_name)

            if len(known_dexes) < 2:
                continue

            quotes = []
            for dex_name in known_dexes:
                router_addr = DEX_ROUTERS.get(dex_name)
                if not router_addr:
                    continue
                amount_out = await oracle.get_amounts_out(
                    router_addr, 10**18, [quote_token, token_addr]
                )
                if amount_out and amount_out > 0:
                    quotes.append(RouterQuote(
                        router_name=dex_name,
                        router_address=router_addr,
                        amount_out=amount_out,
                        price_per_token=float(amount_out),
                    ))

            if len(quotes) < 2:
                continue

            amounts = [(q.router_name, q.amount_out, q.router_address) for q in quotes]
            amounts.sort(key=lambda x: x[1])

            buy_dex, buy_amount, buy_addr = amounts[0]
            sell_dex, sell_amount, sell_addr = amounts[-1]

            if buy_amount == 0:
                continue

            spread_pct = ((sell_amount - buy_amount) / buy_amount) * 100

            if spread_pct < settings.min_spread_pct:
                continue

            buy_pair = await pair_finder.find_pair(
                buy_dex, token_addr, quote_token
            )
            sell_pair = await pair_finder.find_pair(
                sell_dex, token_addr, quote_token
            )

            if not buy_pair or not sell_pair:
                continue

            buy_pool = await pool_fetcher.fetch_pool_reserves(buy_pair.pair_address)
            sell_pool = await pool_fetcher.fetch_pool_reserves(sell_pair.pair_address)

            if not buy_pool or not sell_pool:
                continue

            weth_price_usd = 3800.0
            buy_liq = pool_fetcher.compute_pool_reserve_usd(buy_pool, weth_price_usd)
            sell_liq = pool_fetcher.compute_pool_reserve_usd(sell_pool, weth_price_usd)
            pool_liq = min(buy_liq, sell_liq)

            if pool_liq < settings.min_liquidity_usd:
                continue

            trade_size = min(settings.max_trade_size_usd, pool_liq * 0.1)

            is_verified = True
            try:
                source = await source_fetcher.fetch_source(token_addr)
                is_verified = len(source) > 100
            except Exception:
                is_verified = False

            payload = IngestionPayload(
                run_id=f"{token_symbol.lower()}-{iteration:04d}",
                token_address=token_addr,
                pool_address=buy_pair.pair_address,
                liq_usd=pool_liq,
                is_verified=is_verified,
                gross_spread_pct=spread_pct,
                trade_size_usd=trade_size,
                pool_reserve_usd=pool_liq,
                buy_router=buy_dex,
                sell_router=sell_dex,
            )
            opportunities.append(
                (token_symbol, payload, buy_dex, sell_dex, spread_pct)
            )

            print(
                f"  SPREAD: {token_symbol:10s} | "
                f"{spread_pct:.2f}% | "
                f"buy@{buy_dex}({buy_pair.pair_address[:10]}...) "
                f"sell@{sell_dex}({sell_pair.pair_address[:10]}...) | "
                f"liq=${pool_liq:.0f} trade=${trade_size:.0f} "
                f"[flea market]"
            )

        except Exception as e:
            print(f"  ERROR: {token_symbol}: {e}")

    return opportunities


async def _scan_geckoterminal(
    gecko_discovery: TokenDiscovery,
    oracle: PriceOracle,
    pair_finder: PairFinder,
    pool_fetcher: PoolFetcher,
    settings: Settings,
    source_fetcher: object,
    start_time: float,
    iteration: int,
) -> list[tuple[str, IngestionPayload, str, str, float]]:
    discovered = await gecko_discovery.discover(
        min_liq_usd=settings.min_liquidity_usd,
        max_liq_usd=settings.max_liquidity_usd,
    )

    opportunities = []
    for token in discovered:
        if time.monotonic() - start_time >= DURATION_SECONDS:
            break

        token_addr = token.token_address
        token_symbol = token.token_symbol

        try:
            quote_token = WETH_ADDRESS
            if token.base_symbol == "USDC":
                quote_token = token.base_token

            quotes = []
            for dex_name in token.dexes:
                router_addr = DEX_ROUTERS.get(dex_name)
                if not router_addr:
                    continue
                amount_out = await oracle.get_amounts_out(
                    router_addr, 10**18, [quote_token, token_addr]
                )
                if amount_out and amount_out > 0:
                    quotes.append(RouterQuote(
                        router_name=dex_name,
                        router_address=router_addr,
                        amount_out=amount_out,
                        price_per_token=float(amount_out),
                    ))

            if len(quotes) < 2:
                continue

            amounts = [(q.router_name, q.amount_out, q.router_address) for q in quotes]
            amounts.sort(key=lambda x: x[1])

            buy_dex, buy_amount, buy_addr = amounts[0]
            sell_dex, sell_amount, sell_addr = amounts[-1]

            if buy_amount == 0:
                continue

            spread_pct = ((sell_amount - buy_amount) / buy_amount) * 100

            if spread_pct < settings.min_spread_pct:
                continue

            buy_pair = await pair_finder.find_pair(
                buy_dex, token_addr, quote_token
            )
            sell_pair = await pair_finder.find_pair(
                sell_dex, token_addr, quote_token
            )

            if not buy_pair or not sell_pair:
                continue

            buy_pool = await pool_fetcher.fetch_pool_reserves(buy_pair.pair_address)
            sell_pool = await pool_fetcher.fetch_pool_reserves(sell_pair.pair_address)

            if not buy_pool or not sell_pool:
                continue

            weth_price_usd = 3800.0
            buy_liq = pool_fetcher.compute_pool_reserve_usd(buy_pool, weth_price_usd)
            sell_liq = pool_fetcher.compute_pool_reserve_usd(sell_pool, weth_price_usd)
            pool_liq = min(buy_liq, sell_liq)

            if pool_liq < settings.min_liquidity_usd:
                continue

            trade_size = min(settings.max_trade_size_usd, pool_liq * 0.1)

            is_verified = True
            try:
                source = await source_fetcher.fetch_source(token_addr)
                is_verified = len(source) > 100
            except Exception:
                is_verified = False

            payload = IngestionPayload(
                run_id=f"{token_symbol.lower()}-{iteration:04d}",
                token_address=token_addr,
                pool_address=buy_pair.pair_address,
                liq_usd=pool_liq,
                is_verified=is_verified,
                gross_spread_pct=spread_pct,
                trade_size_usd=trade_size,
                pool_reserve_usd=pool_liq,
                buy_router=buy_dex,
                sell_router=sell_dex,
            )
            opportunities.append(
                (token_symbol, payload, buy_dex, sell_dex, spread_pct)
            )

            print(
                f"  SPREAD: {token_symbol:10s} | "
                f"{spread_pct:.2f}% | "
                f"buy@{buy_dex}({buy_pair.pair_address[:10]}...) "
                f"sell@{sell_dex}({sell_pair.pair_address[:10]}...) | "
                f"liq=${pool_liq:.0f} trade=${trade_size:.0f} "
                f"[gecko]"
            )

        except Exception as e:
            print(f"  ERROR: {token_symbol}: {e}")

    return opportunities


async def main() -> None:
    settings = Settings(dry_run=True)
    setup_logger(settings.log_level)

    print(f"PAPER TRADING — {DURATION_SECONDS}s")
    print(f"  LLM model:    {settings.llm_model_primary}")
    print(f"  CSV output:   {CSV_FILE}")
    print("  Discovery:    flea market + geckoterminal fallback")
    print()

    rpc_rate_limiter = TokenBucketRateLimiter(
        rate=settings.rpc_rate_limit_per_sec,
        capacity=settings.rpc_rate_limit_per_sec,
    )
    rpc_manager = RPCManager(
        primary_url=settings.alchemy_rpc_url or settings.fallback_rpc_url,
        fallback_url=settings.fallback_rpc_url,
        flashbots_url=settings.flashbots_rpc_url,
        rate_limiter=rpc_rate_limiter,
    )

    oracle = PriceOracle(rpc_manager)
    oracle.MIN_RESERVE_USD = 1000.0
    pair_finder = PairFinder(rpc_manager)
    pool_fetcher = PoolFetcher(rpc_manager)
    cache = ContractCache(settings.db_path)
    blacklist_db = BlacklistDB(settings.db_path)
    flea_discovery = FleaMarketDiscovery(rpc_manager, cache)
    dex_client = DexScreenerClient(rate_limit_seconds=4.0)
    gecko_discovery = TokenDiscovery(rpc_manager, pair_finder, dex_client)

    await cache.init()

    class SourceFetcherImpl:
        async def fetch_source(self, token_address: str) -> str:
            return await fetch_contract_source(token_address, settings.arbiscan_api_key)

    source_fetcher = SourceFetcherImpl()
    app = await compile_graph(settings, cache, blacklist_db, source_fetcher=source_fetcher)

    start_time = time.monotonic()
    iteration = 0
    csv_rows: list[dict] = []
    total_opportunities = 0
    total_executed = 0
    total_aborted = 0

    try:
        while time.monotonic() - start_time < DURATION_SECONDS:
            iteration += 1
            elapsed = time.monotonic() - start_time
            remaining = DURATION_SECONDS - elapsed

            print(f"\n{'=' * 70}")
            print(f"  SCAN {iteration}  |  Elapsed: {elapsed:.0f}s  |  Remaining: {remaining:.0f}s")
            print(f"{'=' * 70}")

            scan_start = time.monotonic()

            print("  [1/2] Scanning flea market (new pair deployments)...")
            try:
                opportunities = await _scan_flea_market(
                    flea_discovery, oracle, pair_finder, pool_fetcher,
                    settings, source_fetcher, start_time, iteration,
                )
            except Exception as e:
                print(f"  Flea market scan failed: {e}")
                opportunities = []

            source_label = "flea market"
            if not opportunities:
                print("  [2/2] No flea market targets. Falling back to GeckoTerminal...")
                try:
                    opportunities = await _scan_geckoterminal(
                        gecko_discovery, oracle, pair_finder, pool_fetcher,
                        settings, source_fetcher, start_time, iteration,
                    )
                except Exception as e:
                    print(f"  GeckoTerminal scan failed: {e}")
                    opportunities = []
                source_label = "geckoterminal"
            else:
                print(f"  Flea market found {len(opportunities)} opportunities")

            scan_time = time.monotonic() - scan_start
            total_opportunities += len(opportunities)

            print(
                f"\n  Found {len(opportunities)} opportunities "
                f"in {scan_time:.1f}s [{source_label}]"
            )

            for token_symbol, payload, buy_dex, sell_dex, spread_pct in opportunities:
                if time.monotonic() - start_time >= DURATION_SECONDS:
                    break

                state = create_state_from_payload(payload, dry_run=True)

                t0 = time.monotonic()
                try:
                    result = await app.ainvoke(
                        state.model_dump(),
                        config={"configurable": {"thread_id": payload.run_id}},
                    )
                    latency = time.monotonic() - t0

                    status = result.get("status", "UNKNOWN")
                    row = {
                        "iteration": iteration,
                        "token": token_symbol,
                        "run_id": payload.run_id,
                        "token_address": payload.token_address,
                        "pool_address": payload.pool_address,
                        "spread_pct": spread_pct,
                        "buy_router": buy_dex,
                        "sell_router": sell_dex,
                        "pool_reserve_usd": payload.pool_reserve_usd,
                        "trade_size_usd": payload.trade_size_usd,
                        "status": status,
                        "slippage_pct": result.get("expected_slippage_pct", ""),
                        "net_profit_usd": result.get("net_profit_usd", ""),
                        "tx_hash": result.get("tx_hash", ""),
                        "reason": result.get("reason", ""),
                        "latency_ms": round(latency * 1000, 1),
                        "source": source_label,
                    }
                    csv_rows.append(row)

                    if status == "EXECUTED":
                        total_executed += 1
                        profit = result.get("net_profit_usd", 0)
                        slippage = result.get("expected_slippage_pct", 0)
                        print(
                            f"  PAPER: {token_symbol} | "
                            f"spread={spread_pct:.2f}% "
                            f"profit=${profit:.4f} "
                            f"slippage={slippage:.2f}%"
                        )
                    else:
                        total_aborted += 1
                        reason = str(result.get("reason", "N/A"))[:80]
                        print(f"  SKIP:  {token_symbol} | {status} | {reason}")

                except Exception as e:
                    total_aborted += 1
                    csv_rows.append({
                        "iteration": iteration,
                        "token": token_symbol,
                        "run_id": payload.run_id,
                        "token_address": payload.token_address,
                        "pool_address": payload.pool_address,
                        "spread_pct": spread_pct,
                        "buy_router": buy_dex,
                        "sell_router": sell_dex,
                        "pool_reserve_usd": payload.pool_reserve_usd,
                        "trade_size_usd": payload.trade_size_usd,
                        "status": "ERROR",
                        "slippage_pct": "",
                        "net_profit_usd": "",
                        "tx_hash": "",
                        "reason": str(e)[:80],
                        "latency_ms": 0,
                        "source": source_label,
                    })
                    print(f"  ERROR: {token_symbol}: {e}")

            with open(CSV_FILE, "w", newline="") as f:
                if csv_rows:
                    writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
                    writer.writeheader()
                    writer.writerows(csv_rows)

            print(
                f"\n  Scan {iteration} done. "
                f"Opps: {total_opportunities} "
                f"Paper: {total_executed} "
                f"Skip: {total_aborted}"
            )

            sleep_time = max(
                0, settings.scanner_scan_interval - (time.monotonic() - scan_start)
            )
            if sleep_time > 0 and time.monotonic() - start_time < DURATION_SECONDS:
                await asyncio.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")

    elapsed_total = time.monotonic() - start_time
    print(f"\n{'=' * 70}")
    print("  PAPER TRADING SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Duration:      {elapsed_total:.0f}s")
    print(f"  Scans:         {iteration}")
    print(f"  Opportunities: {total_opportunities}")
    print(f"  Paper trades:  {total_executed}")
    print(f"  Skipped:       {total_aborted}")
    print(f"  CSV:           {CSV_FILE}")
    print(f"{'=' * 70}")

    await dex_client.close()
    await rpc_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
