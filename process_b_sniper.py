"""
Process B: The Triangular Arbitrage Sniper (Latency Critical)

Single-DEX triangular routing: WETH -> EXOTIC -> USDC -> WETH.
Math-first execution: simulate before audit, audit before sign.

Flow:
B1: Triangular Path Scanner
B2: Quantitative Profit Gate (local x*y=k math)
B3: On-chain Simulation Gate (eth_call)
B4: Honeypot Detection (revert analysis)
B5: LLM Security Audit (conditional — only if profitable + simulates)
B6: Execution Gatekeeper (Flashbots broadcast)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

from config.constants import (
    DEX_ROUTERS,
    USDC_ADDRESS,
    USDT_ADDRESS,
    WETH_ADDRESS,
)
from db.cleared_tokens import ClearedTokensDB
from infra.local_pricing import (
    ExecutionState,
    PoolInfo,
    QuotePoolInfo,
    TriangularPath,
    compute_net_profit,
    compute_v2_output,
    find_triangular_path,
)
from infra.price_oracle import WETHPriceOracle
from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)


class TriangularScanner:
    """Discovers triangular paths on a single DEX for a given exotic token."""

    def __init__(
        self,
        rpc_manager: RPCManager,
        cleared_db: ClearedTokensDB,
        weth_oracle: WETHPriceOracle | None = None,
    ) -> None:
        self.rpc = rpc_manager
        self.db = cleared_db
        self.oracle = weth_oracle or WETHPriceOracle(rpc_manager)
        self._reserves_cache: dict[str, tuple[int, int]] = {}
        self._cache_ttl: dict[str, float] = {}
        self._token0_cache: dict[str, str] = {}
        self.CACHE_DURATION = 5.0

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

    async def _get_token0(self, pair_address: str) -> str | None:
        if pair_address in self._token0_cache:
            return self._token0_cache[pair_address]

        try:
            raw = await self.rpc.call_contract(pair_address, "0x0dfe1681")
            if not raw or raw == "0x":
                return None
            token0 = "0x" + raw[-40:]
            self._token0_cache[pair_address] = token0
            return token0
        except Exception:
            return None

    async def scan(
        self,
        min_spread_pct: float = 0.5,
        trade_size_usd: float = 10.0,
    ) -> list[TriangularPath]:
        cleared_tokens = self.db.get_cleared_tokens()
        if not cleared_tokens:
            return []

        weth_price = await self._get_weth_price()
        if weth_price is None:
            logger.error("WETH oracle unavailable — aborting scan cycle")
            return []

        input_weth_wei = int((trade_size_usd / weth_price) * 1e18)

        weth_usdc_pool = await self._find_weth_usdc_pool()
        if weth_usdc_pool is None:
            logger.warning("No WETH/USDC pool found on any DEX — cannot route")
            return []

        token_addresses = list(set(t.token_address for t in cleared_tokens))
        all_paths: list[TriangularPath] = []

        for token_addr in token_addresses:
            token_pairs = [t for t in cleared_tokens if t.token_address == token_addr]
            if not token_pairs:
                continue

            dex_groups: dict[str, list] = {}
            for pair in token_pairs:
                dex_groups.setdefault(pair.dex_name, []).append(pair)

            for dex_name, pairs in dex_groups.items():
                path = await self._find_path_for_token(
                    token_addr=token_addr,
                    dex_name=dex_name,
                    pairs=pairs,
                    input_weth_wei=input_weth_wei,
                    weth_usdc_pool=weth_usdc_pool,
                    weth_price=weth_price,
                )
                if path and path.gross_spread_pct >= min_spread_pct:
                    all_paths.append(path)

        return sorted(all_paths, key=lambda p: p.gross_spread_pct, reverse=True)

    async def _find_path_for_token(
        self,
        token_addr: str,
        dex_name: str,
        pairs: list,
        input_weth_wei: int,
        weth_usdc_pool: PoolInfo,
        weth_price: float,
    ) -> TriangularPath | None:
        weth_pool = None
        quote_pools: dict[str, QuotePoolInfo] = {}

        for pair in pairs:
            reserves = await self._get_reserves(pair.pair_address)
            if not reserves or reserves[0] == 0 or reserves[1] == 0:
                continue

            token0 = await self._get_token0(pair.pair_address)
            if not token0:
                continue

            t0_lower = token0.lower()
            weth_lower = WETH_ADDRESS.lower()
            usdc_lower = USDC_ADDRESS.lower()
            usdt_lower = USDT_ADDRESS.lower()

            if t0_lower == weth_lower or pair.token1.lower() == weth_lower:
                weth_is_r0 = t0_lower == weth_lower
                weth_pool = PoolInfo(
                    weth_is_r0=weth_is_r0,
                    reserves=reserves,
                    pair_address=pair.pair_address,
                )
            elif t0_lower == usdc_lower or pair.token1.lower() == usdc_lower:
                quote_is_r0 = t0_lower == usdc_lower
                quote_pools["USDC"] = QuotePoolInfo(
                    quote_is_r0=quote_is_r0,
                    reserves=reserves,
                    pair_address=pair.pair_address,
                    quote_symbol="USDC",
                )
            elif t0_lower == usdt_lower or pair.token1.lower() == usdt_lower:
                quote_is_r0 = t0_lower == usdt_lower
                quote_pools["USDT"] = QuotePoolInfo(
                    quote_is_r0=quote_is_r0,
                    reserves=reserves,
                    pair_address=pair.pair_address,
                    quote_symbol="USDT",
                )

        if weth_pool is None or not quote_pools:
            return None

        paths = find_triangular_path(
            token_address=token_addr,
            token_symbol=pairs[0].symbol or token_addr[:10],
            dex_name=dex_name,
            weth_pool=weth_pool,
            quote_pools=quote_pools,
            weth_usdc_pool=weth_usdc_pool,
            input_weth_wei=input_weth_wei,
        )

        return paths[0] if paths else None

    async def _find_weth_usdc_pool(self) -> PoolInfo | None:
        from infra.create2 import compute_v2_pair_address

        dex_names = ["uniswap_v2", "sushiswap", "camelot_v2"]
        for dex_name in dex_names:
            pair = compute_v2_pair_address(dex_name, WETH_ADDRESS, USDC_ADDRESS)
            if not pair:
                continue

            reserves = await self._get_reserves(pair.pair_address)
            if not reserves or reserves[0] == 0 or reserves[1] == 0:
                continue

            token0 = await self._get_token0(pair.pair_address)
            if not token0:
                continue

            weth_is_r0 = token0.lower() == WETH_ADDRESS.lower()
            return PoolInfo(
                weth_is_r0=weth_is_r0,
                reserves=reserves,
                pair_address=pair.pair_address,
            )

        return None

    async def _get_weth_price(self) -> float | None:
        try:
            return await self.oracle.get_weth_price()
        except ValueError:
            return None


class Simulator:
    """On-chain eth_call simulation for triangular swap routes."""

    def __init__(self, rpc_manager: RPCManager) -> None:
        self.rpc = rpc_manager

    async def simulate_triangular(
        self,
        path: TriangularPath,
        trade_size_usd: float,
        weth_price: float,
    ) -> tuple[bool, str, int]:
        """
        Simulate the triangular swap via eth_call.

        Returns (success, revert_reason, simulated_output_weth).
        """
        input_weth_wei = path.input_weth_wei

        exotic_amount = compute_v2_output(
            reserve_in=path._weth_pool_weth_reserve
            if hasattr(path, "_weth_pool_weth_reserve")
            else 0,
            reserve_out=path._weth_pool_exotic_reserve
            if hasattr(path, "_weth_pool_exotic_reserve")
            else 0,
            amount_in=input_weth_wei,
        )

        if exotic_amount == 0:
            return False, "zero exotic output from math", 0

        try:
            router = DEX_ROUTERS.get(path.dex_name, "")
            if not router:
                return False, f"no router for {path.dex_name}", 0

            calldata = self._build_triangular_calldata(path, input_weth_wei)

            raw_result = await self.rpc.call_contract(router, calldata)

            if not raw_result or raw_result == "0x" or raw_result == "0x0":
                return False, "eth_call returned empty", 0

            output_weth = int(raw_result, 16)

            if output_weth <= input_weth_wei:
                return False, f"output {output_weth} <= input {input_weth_wei}", 0

            return True, "", output_weth

        except Exception as e:
            error_str = str(e).lower()
            if any(
                kw in error_str
                for kw in [
                    "revert",
                    "honeypot",
                    "pause",
                    "blacklist",
                    "transfer",
                    "forbidden",
                    "disabled",
                ]
            ):
                return False, f"honeypot revert: {e}", 0
            return False, f"simulation error: {e}", 0

    def _build_triangular_calldata(
        self, path: TriangularPath, amount_in: int
    ) -> str:
        selector = "0x38ed1739"
        amount_in_hex = format(amount_in, "064x")
        amount_out_min = format(0, "064x")
        path_data = "".join(
            addr.lower().replace("0x", "").zfill(64)
            for addr in [
                WETH_ADDRESS,
                path.token_address,
                USDC_ADDRESS,
                WETH_ADDRESS,
            ]
        )
        to_addr = WETH_ADDRESS.lower().replace("0x", "").zfill(64)
        deadline = format(int(time.time()) + 300, "064x")

        return (
            selector
            + amount_in_hex
            + amount_out_min
            + "00000000000000000000000000000000000000000000000000000000000000a0"
            + format(3, "064x")
            + path_data
            + to_addr
            + deadline
        )


class LLMSecurityAuditor:
    """Conditional LLM audit — only invoked after math + simulation pass."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.groq.com/openai/v1"

    async def audit(self, minified_source: str) -> tuple[bool, list[str]]:
        if not minified_source or len(minified_source) < 100:
            return False, ["insufficient source code"]

        system_prompt = (
            "You are a Solidity security auditor. "
            "You will receive minified smart contract source code.\n\n"
            "Your task: inspect the code for these vulnerability classes:\n"
            "1. Hidden transfer taxes (fees on transfers not disclosed)\n"
            "2. Malicious mint mechanisms (unrestricted owner-only minting)\n"
            "3. Freeze or blacklist parameters (lock user funds or block)\n"
            "4. Balance modification vulnerabilities (direct manipulation)\n"
            "5. Honeypot patterns (buy allowed, sell blocked or penalized)\n"
            "6. Upgradeable proxies (admin can change logic post-deployment)\n\n"
            'Respond with JSON: {"is_safe": bool, "threats": [str]}\n'
            "- is_safe=true ONLY if NONE detected.\n"
            "- threats=empty list if none.\n"
            "RULES: Output ONLY JSON. No markdown, no prose.\n"
            "If code too short: is_safe=false, "
            'threats=["insufficient source code"].'
        )

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": minified_source[:8000]},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 500,
                    },
                )
                response.raise_for_status()
                data = response.json()

                content = data["choices"][0]["message"]["content"].strip()

                if content.startswith("```"):
                    content = content.split("\n", 1)[1]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()

                result = json.loads(content)
                is_safe = result.get("is_safe", False)
                threats = result.get("threats", [])
                return is_safe, threats

        except Exception as e:
            logger.error("LLM audit failed: %s", e)
            return False, [f"audit failed: {e}"]


class ProcessBSniper:
    def __init__(
        self,
        cleared_db: ClearedTokensDB,
        rpc_manager: RPCManager,
        trade_size_usd: float = 10.0,
        gas_usd: float = 0.02,
        min_spread_pct: float = 0.5,
        min_net_profit_usd: float = 0.50,
        dry_run: bool = True,
        live_executor=None,
        llm_api_key: str = "",
        llm_model: str = "llama-3.3-70b-versatile",
        on_opportunity: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self.oracle = WETHPriceOracle(rpc_manager)
        self.scanner = TriangularScanner(rpc_manager, cleared_db, self.oracle)
        self.simulator = Simulator(rpc_manager)
        self.trade_size_usd = trade_size_usd
        self.gas_usd = gas_usd
        self.min_spread_pct = min_spread_pct
        self.min_net_profit_usd = min_net_profit_usd
        self.dry_run = dry_run
        self.live_executor = live_executor
        self.on_opportunity = on_opportunity

        self.llm_auditor = (
            LLMSecurityAuditor(llm_api_key, llm_model) if llm_api_key else None
        )

        self._running = False
        self._results: list[ExecutionState] = []
        self._weth_price: float | None = None

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
        paths = await self.scanner.scan(
            min_spread_pct=self.min_spread_pct,
            trade_size_usd=self.trade_size_usd,
        )

        self._weth_price = await self.scanner._get_weth_price()
        if self._weth_price is None:
            return

        for path in paths:
            state = await self._evaluate_path(path)
            if state:
                self._results.append(state)

    async def _evaluate_path(self, path: TriangularPath) -> ExecutionState | None:
        trade_id = f"{path.token_address[:10]}_{int(time.time() * 1000)}"

        state = ExecutionState(
            trade_id=trade_id,
            token_address=path.token_address,
            token_symbol=path.token_symbol,
            dex_name=path.dex_name,
            weth_to_exotic_pool=path.weth_to_exotic_pool,
            exotic_to_quote_pool=path.exotic_to_quote_pool,
            quote_to_weth_pool=path.quote_to_weth_pool,
            input_weth_wei=path.input_weth_wei,
            output_weth_wei=path.output_weth_wei,
            gross_spread_pct=path.gross_spread_pct,
            trade_size_usd=self.trade_size_usd,
            gas_overhead_usd=self.gas_usd,
            net_profit_usd=0.0,
            quote_symbol=path.quote_symbol,
            exotic_amount_wei=path.exotic_amount_wei,
            quote_amount_raw=path.quote_amount_raw,
        )

        gas_baseline_eth = self.gas_usd / self._weth_price
        gas_baseline_wei = int(gas_baseline_eth * 1e18)

        if path.output_weth_wei <= path.input_weth_wei + gas_baseline_wei:
            net_profit, _ = compute_net_profit(
                path.gross_spread_pct, self.trade_size_usd, self.gas_usd
            )
            state.net_profit_usd = net_profit
            state.status = "ABORTED"
            state.abort_reason = (
                f"math gate: output {path.output_weth_wei} <= "
                f"input+gas {path.input_weth_wei + gas_baseline_wei}"
            )
            logger.debug(
                "MATH GATE: %s spread=%.2f%% output<=input+gas",
                path.token_address[:10],
                path.gross_spread_pct,
            )
            return state

        logger.info(
            "MATH PASS: %s spread=%.2f%% on %s",
            path.token_address[:10],
            path.gross_spread_pct,
            path.dex_name,
        )

        if self.on_opportunity:
            net_profit_math, _ = compute_net_profit(
                path.gross_spread_pct, self.trade_size_usd, self.gas_usd
            )
            await self.on_opportunity(
                token_address=path.token_address,
                dex=path.dex_name,
                spread_pct=path.gross_spread_pct,
                net_profit=net_profit_math,
                trade_size=self.trade_size_usd,
                stage="MATH",
            )

        sim_success, sim_reason, sim_output = (
            await self.simulator.simulate_triangular(
                path, self.trade_size_usd, self._weth_price
            )
        )

        if not sim_success:
            state.eth_call_success = False
            state.eth_call_revert_reason = sim_reason
            state.status = "ABORTED"
            state.abort_reason = f"simulation failed: {sim_reason}"

            if "honeypot" in sim_reason.lower() or "revert" in sim_reason.lower():
                logger.warning(
                    "HONEYPOT: %s — %s",
                    path.token_address[:10],
                    sim_reason,
                )
            else:
                logger.debug(
                    "SIM FAIL: %s — %s",
                    path.token_address[:10],
                    sim_reason,
                )
            return state

        state.eth_call_success = True
        state.output_weth_wei = sim_output
        state.output_weth_eth = sim_output / 1e18

        net_profit, is_profitable = compute_net_profit(
            path.gross_spread_pct, self.trade_size_usd, self.gas_usd
        )
        state.net_profit_usd = net_profit

        if not is_profitable:
            state.status = "ABORTED"
            state.abort_reason = f"net profit ${net_profit:.4f} below floor"
            return state

        logger.info(
            "SIM PASS: %s profit=$%.4f — running LLM audit",
            path.token_address[:10],
            net_profit,
        )

        if self.on_opportunity:
            await self.on_opportunity(
                token_address=path.token_address,
                dex=path.dex_name,
                spread_pct=path.gross_spread_pct,
                net_profit=net_profit,
                trade_size=self.trade_size_usd,
                stage="SIM",
            )

        if self.llm_auditor:
            from agents.minifier import minify_solidity
            from infra.source_fetcher import SourceFetcher

            fetcher = SourceFetcher()
            source = await fetcher.fetch_source(path.token_address)

            if source:
                minified = minify_solidity(source)
                is_safe, threats = await self.llm_auditor.audit(minified)
                state.audit_is_safe = is_safe
                state.audit_threats = threats

                if not is_safe:
                    state.status = "ABORTED"
                    state.abort_reason = f"LLM audit failed: {threats}"
                    logger.warning(
                        "LLM FAIL: %s — %s",
                        path.token_address[:10],
                        threats,
                    )
                    return state

                logger.info("LLM PASS: %s", path.token_address[:10])
            else:
                logger.warning(
                    "No source for %s — skipping LLM audit",
                    path.token_address[:10],
                )
                state.audit_is_safe = True
                state.audit_threats = []
        else:
            state.audit_is_safe = True
            state.audit_threats = []

        state.status = "AUTHORIZED"

        if self.dry_run:
            state.status = "EXECUTED"
            logger.info(
                "PAPER TRADE: %s | spread=%.2f%% | net=$%.4f | %s",
                path.token_address[:10],
                path.gross_spread_pct,
                net_profit,
                path.dex_name,
            )
            return state

        if self.live_executor:
            return await self._execute_live(state, path)

        state.status = "ABORTED"
        state.abort_reason = "live execution not configured"
        return state

    async def _execute_live(
        self, state: ExecutionState, path: TriangularPath
    ) -> ExecutionState:
        try:
            from infra.live_executor import SwapParams

            amount_in_eth = state.trade_size_usd / self._weth_price

            params = SwapParams(
                token_in=WETH_ADDRESS,
                token_out=state.token_address,
                amount_in=state.input_weth_wei,
                amount_out_min=0,
                path=[WETH_ADDRESS, state.token_address],
                to=state.token_address,
                deadline=int(time.time()) + 300,
            )

            router_address = DEX_ROUTERS.get(state.dex_name, "")
            if not router_address:
                state.status = "ABORTED"
                state.abort_reason = f"no router for {state.dex_name}"
                return state

            result = await self.live_executor.execute_swap(
                router_address=router_address,
                params=params,
                value_eth=amount_in_eth,
            )

            if result.status == "SUBMITTED":
                state.status = "EXECUTED"
                state.tx_hash = result.tx_hash
                logger.info(
                    "LIVE TRADE: %s tx=%s",
                    state.token_address[:10],
                    result.tx_hash[:20],
                )
            else:
                state.status = "ABORTED"
                state.abort_reason = result.error or "execution failed"

        except Exception as e:
            state.status = "ABORTED"
            state.abort_reason = f"execution error: {e}"

        return state

    def get_results(self) -> list[ExecutionState]:
        return self._results

    def stop(self) -> None:
        self._running = False
        logger.info("Process B: Sniper stopped")
