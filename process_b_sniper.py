"""
Process B: The Execution Loop (Latency Critical)

Ultra-fast, deterministic, mathematical. Runs in microseconds to execute trades.
Only looks at tokens that have already been cleared by Process A.
No external API calls or LLM invocations exist in this loop.

Flow:
B1: Price Anomaly Scanner → B2: Quant Analyst → B3: Execution Gatekeeper & Builder
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal, TypedDict

from db.cleared_tokens import ClearedTokensDB
from infra.local_pricing import (
    compute_net_profit,
    compute_v2_output,
    find_local_spreads,
)
from infra.pool_fetcher import PoolFetcher
from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)


class ExecutionState(TypedDict):
    trade_id: str
    token_address: str
    buy_pool: str
    sell_pool: str
    buy_dex: str
    sell_dex: str
    spread_pct: float
    trade_size_usd: float
    exact_output_usd: float
    gas_overhead_usd: float
    net_profit_usd: float
    status: Literal["PENDING", "EXECUTED", "ABORTED"]
    abort_reason: str


@dataclass
class TradeOpportunity:
    token_address: str
    token_symbol: str
    buy_dex: str
    buy_pair: str
    sell_dex: str
    sell_pair: str
    spread_pct: float
    buy_reserves: tuple[int, int]
    sell_reserves: tuple[int, int]
    liquidity_usd: float


class PriceAnomalyScanner:
    WETH_USDC_POOL = "0x969F4E88c0eC4FEB8A1F6eBd46ba1393C2D11A57"
    USDC_DECIMALS = 6
    WETH_DECIMALS = 18

    def __init__(
        self,
        cleared_db: ClearedTokensDB,
        pool_fetcher: PoolFetcher,
        rpc_manager: RPCManager,
    ) -> None:
        self.db = cleared_db
        self.pool_fetcher = pool_fetcher
        self.rpc = rpc_manager
        self._reserves_cache: dict[str, tuple[int, int]] = {}
        self._cache_ttl: dict[str, float] = {}
        self.CACHE_DURATION = 5.0
        self._weth_price_usd: float = 3800.0
        self._weth_price_ttl: float = 0.0
        self.WETH_PRICE_CACHE = 60.0

    async def _get_weth_price(self) -> float:
        now = time.time()
        if now - self._weth_price_ttl < self.WETH_PRICE_CACHE:
            return self._weth_price_usd

        try:
            raw = await self.rpc.call_contract(self.WETH_USDC_POOL, "0x0902f1ac")
            data = bytes.fromhex(raw.replace("0x", ""))
            r0 = int.from_bytes(data[0:32], "big")
            r1 = int.from_bytes(data[32:64], "big")

            token0_raw = await self.rpc.call_contract(self.WETH_USDC_POOL, "0x0dfe1681")
            token0 = "0x" + token0_raw[-40:]

            weth_addr = "0x82af49447d8a07e3bd95bd0d56f35241523fab1"
            if token0.lower() == weth_addr:
                weth_reserve = r0
                usdc_reserve = r1
            else:
                weth_reserve = r1
                usdc_reserve = r0

            if weth_reserve > 0 and usdc_reserve > 0:
                self._weth_price_usd = (usdc_reserve / 10**self.USDC_DECIMALS) / (
                    weth_reserve / 10**self.WETH_DECIMALS
                )
                self._weth_price_ttl = now
                logger.debug("WETH price updated: $%.2f", self._weth_price_usd)
        except Exception as e:
            logger.debug(
                "Failed to fetch WETH price, using cached $%.2f: %s",
                self._weth_price_usd, e,
            )

        return self._weth_price_usd

    async def scan(self, min_spread_pct: float = 2.0) -> list[TradeOpportunity]:
        cleared_tokens = self.db.get_cleared_tokens()
        if not cleared_tokens:
            return []

        weth_price = await self._get_weth_price()
        token_addresses = list(set(t.token_address for t in cleared_tokens))
        opportunities = []

        for token_addr in token_addresses:
            token_pairs = [t for t in cleared_tokens if t.token_address == token_addr]
            if len(token_pairs) < 2:
                continue

            pools: dict[str, tuple[int, int]] = {}
            for pair in token_pairs:
                reserves = await self._get_reserves(pair.pair_address)
                if reserves and reserves[0] > 0 and reserves[1] > 0:
                    pools[pair.dex_name] = reserves
                else:
                    logger.debug("No reserves for %s on %s", pair.symbol, pair.dex_name)

            if len(pools) < 2:
                logger.debug("Not enough pools for %s: %d", token_addr[:10], len(pools))
                continue

            token_symbol = token_pairs[0].symbol or token_addr[:10]
            logger.debug("Scanning %s across %d DEXes", token_symbol, len(pools))

            local_opps = find_local_spreads(
                token_address=token_addr,
                token_symbol=token_symbol,
                pools=pools,
                min_spread_pct=min_spread_pct,
            )

            for opp in local_opps:
                liquidity = self._estimate_liquidity_usd(pools, weth_price)
                opportunities.append(
                    TradeOpportunity(
                        token_address=opp.token_address,
                        token_symbol=opp.token_symbol,
                        buy_dex=opp.buy_dex,
                        buy_pair=opp.buy_pair,
                        sell_dex=opp.sell_dex,
                        sell_pair=opp.sell_pair,
                        spread_pct=opp.spread_pct,
                        buy_reserves=opp.buy_reserves,
                        sell_reserves=opp.sell_reserves,
                        liquidity_usd=liquidity,
                    )
                )

        return sorted(opportunities, key=lambda x: x.spread_pct, reverse=True)

    async def _get_reserves(self, pair_address: str) -> tuple[int, int] | None:
        now = time.time()
        if pair_address in self._reserves_cache:
            if now - self._cache_ttl.get(pair_address, 0) < self.CACHE_DURATION:
                return self._reserves_cache[pair_address]

        try:
            raw = await self.rpc.call_contract(pair_address, "0x0902f1ac")
            data = bytes.fromhex(raw.replace("0x", ""))
            r0 = int.from_bytes(data[0:32], "big")
            r1 = int.from_bytes(data[32:64], "big")

            self._reserves_cache[pair_address] = (r0, r1)
            self._cache_ttl[pair_address] = now
            return (r0, r1)
        except Exception as e:
            logger.debug("Failed to fetch reserves for %s: %s", pair_address[:10], e)
            return None

    def _estimate_liquidity_usd(
        self, pools: dict[str, tuple[int, int]], weth_price: float
    ) -> float:
        total = 0.0
        for reserves in pools.values():
            weth_reserve = reserves[1] / 1e18
            total += weth_reserve * weth_price
        return total / len(pools) if pools else 0.0


class QuantAnalyst:
    def __init__(self, trade_size_usd: float = 200.0, gas_usd: float = 0.02) -> None:
        self.trade_size_usd = trade_size_usd
        self.gas_usd = gas_usd
        self.weth_price_usd: float = 3800.0

    def evaluate(self, opportunity: TradeOpportunity) -> ExecutionState | None:
        weth_price = self.weth_price_usd

        buy_amount_in = int((self.trade_size_usd / weth_price) * 1e18)

        amount_out = compute_v2_output(
            reserve_in=opportunity.buy_reserves[1],
            reserve_out=opportunity.buy_reserves[0],
            amount_in=buy_amount_in,
        )

        if amount_out == 0:
            return None

        sell_amount_out = compute_v2_output(
            reserve_in=opportunity.sell_reserves[0],
            reserve_out=opportunity.sell_reserves[1],
            amount_in=amount_out,
        )

        if sell_amount_out == 0:
            return None

        output_usd = (sell_amount_out / 1e18) * weth_price

        net_profit, is_profitable = compute_net_profit(
            spread_pct=opportunity.spread_pct,
            trade_size_usd=self.trade_size_usd,
            gas_usd=self.gas_usd,
        )

        trade_id = f"{opportunity.token_address[:10]}_{int(time.time() * 1000)}"

        return ExecutionState(
            trade_id=trade_id,
            token_address=opportunity.token_address,
            buy_pool=opportunity.buy_pair,
            sell_pool=opportunity.sell_pair,
            buy_dex=opportunity.buy_dex,
            sell_dex=opportunity.sell_dex,
            spread_pct=opportunity.spread_pct,
            trade_size_usd=self.trade_size_usd,
            exact_output_usd=output_usd,
            gas_overhead_usd=self.gas_usd,
            net_profit_usd=net_profit,
            status="PENDING" if is_profitable else "ABORTED",
            abort_reason="" if is_profitable else f"net profit ${net_profit:.4f} below $0.50 floor",
        )


class ExecutionGatekeeper:
    def __init__(self, dry_run: bool = True, live_executor=None) -> None:
        self.dry_run = dry_run
        self.live_executor = live_executor
        self.weth_price_usd: float = 3800.0

    async def execute(self, state: ExecutionState) -> ExecutionState:
        if state["status"] == "ABORTED":
            return state

        if state["net_profit_usd"] < 0.50:
            state["status"] = "ABORTED"
            state["abort_reason"] = f"profit ${state['net_profit_usd']:.4f} below floor"
            return state

        if self.dry_run:
            state["status"] = "EXECUTED"
            logger.info(
                "PAPER TRADE: %s | spread=%.2f%% | net=$%.4f",
                state["token_address"][:10],
                state["spread_pct"],
                state["net_profit_usd"],
            )
            return state

        if self.live_executor:
            from config.constants import DEX_ROUTERS, WETH_ADDRESS
            from infra.live_executor import SwapParams

            weth_price = self.weth_price_usd
            amount_in_eth = state["trade_size_usd"] / weth_price

            params = SwapParams(
                token_in=WETH_ADDRESS,
                token_out=state["token_address"],
                amount_in=int(amount_in_eth * 1e18),
                amount_out_min=0,
                path=[WETH_ADDRESS, state["token_address"]],
                to=state.get("buyer_address", ""),
                deadline=int(time.time()) + 300,
            )

            router_address = DEX_ROUTERS.get(state["buy_dex"], "")
            if not router_address:
                state["status"] = "ABORTED"
                state["abort_reason"] = f"no router for dex {state['buy_dex']}"
                return state

            result = await self.live_executor.execute_swap(
                router_address=router_address,
                params=params,
                value_eth=amount_in_eth,
            )

            if result.status == "SUBMITTED":
                state["status"] = "EXECUTED"
                logger.info(
                    "LIVE TRADE: %s tx=%s",
                    state["token_address"][:10],
                    result.tx_hash[:20],
                )
            else:
                state["status"] = "ABORTED"
                state["abort_reason"] = result.error or "execution failed"

            return state

        state["status"] = "ABORTED"
        state["abort_reason"] = "live execution not implemented"
        return state


class ProcessBSniper:
    def __init__(
        self,
        cleared_db: ClearedTokensDB,
        pool_fetcher: PoolFetcher,
        rpc_manager: RPCManager,
        trade_size_usd: float = 200.0,
        gas_usd: float = 0.02,
        min_spread_pct: float = 2.0,
        dry_run: bool = True,
        live_executor=None,
    ) -> None:
        self.scanner = PriceAnomalyScanner(cleared_db, pool_fetcher, rpc_manager)
        self.quant = QuantAnalyst(trade_size_usd, gas_usd)
        self.gatekeeper = ExecutionGatekeeper(dry_run, live_executor)
        self.min_spread_pct = min_spread_pct
        self._running = False
        self._results: list[ExecutionState] = []

    async def run(self, scan_interval: float = 1.0) -> None:
        self._running = True
        logger.info("Process B: Sniper started (interval=%.1fs)", scan_interval)

        while self._running:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error("Sniper scan cycle failed: %s", e)

            await asyncio.sleep(scan_interval)

    async def _scan_cycle(self) -> None:
        opportunities = await self.scanner.scan(self.min_spread_pct)
        self.quant.weth_price_usd = self.scanner._weth_price_usd
        self.gatekeeper.weth_price_usd = self.scanner._weth_price_usd

        for opp in opportunities:
            state = self.quant.evaluate(opp)
            if not state:
                continue

            result = await self.gatekeeper.execute(state)
            self._results.append(result)

            if result["status"] == "EXECUTED":
                logger.info(
                    "TRADE EXECUTED: %s | buy@%s sell@%s | spread=%.2f%% | net=$%.4f",
                    result["token_address"][:10],
                    result["buy_pool"][:10],
                    result["sell_pool"][:10],
                    result["spread_pct"],
                    result["net_profit_usd"],
                )

    def get_results(self) -> list[ExecutionState]:
        return self._results

    def stop(self) -> None:
        self._running = False
        logger.info("Process B: Sniper stopped")
