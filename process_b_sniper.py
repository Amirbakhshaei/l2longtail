"""
Process B: The Arbitrage Sniper (Latency Critical)

Two execution pipelines share this module:

1. Triangular (legacy): WETH -> EXOTIC -> USDC -> WETH on a single DEX.
   Math-first, simulate-before-audit, audit-before-sign.

2. Graph (Phase 2-4): N-hop arbitrage over a dynamic token graph.
   - Bellman-Ford detects negative-weight cycles (profitable loops).
   - Ternary Search sizes the exact WETH input that maximises net profit.
   - Balancer V2 flashloans (0% fee venue on Arbitrum) remove inventory
     caps; the trade is simulated via zero-gas eth_call then broadcast.

Flow (graph):
    Sync queue → update graph edge → Bellman-Ford → Ternary Search
    → eth_call simulation → (audit) → flashloan execution
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

from eth_abi import encode as abi_encode
from eth_utils import keccak

from config.constants import (
    DEX_ROUTERS,
    USDC_ADDRESS,
    USDT_ADDRESS,
    WETH_ADDRESS,
)
from db.cleared_tokens import ClearedTokensDB
from infra.flea_market_discovery import V3StateEvent
from infra.local_pricing import (
    ArbCycle,
    DirectedGraph,
    ExecutionState,
    PoolEdge,
    PoolInfo,
    QuotePoolInfo,
    TriangularPath,
    V3PoolEdge,
    compute_net_profit,
    compute_v2_output,
    find_triangular_path,
)
from infra.onchain_pricing import OnChainQuoter
from infra.price_oracle import WETHPriceOracle
from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"

# Human-readable DEX labels for structured path telemetry.
DEX_LABELS = {
    "uniswap_v2": "UniV2",
    "uniswap_v3": "UniV3",
    "sushiswap": "SushiV2",
    "camelot_v2": "CamelotV2",
    "camelot_v3": "CamelotV3",
    "trader_joe": "TraderJoe",
}


def _token_label(addr: str) -> str:
    """Short symbol-ish label for a token address in logs."""
    a = addr.lower()
    known = {
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": "WETH",
        "0xaf88d065e77c8c2239327c5edb3a432268e5831": "USDC",
        "0xfd086bc7cd5c481dcc9c85e478a1c0b69fcbb9": "USDT",
    }
    if a in known:
        return known[a]
    return addr[:8]


# ===========================================================================
# Legacy triangular pipeline (unchanged behaviour)
# ===========================================================================
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
        min_spread_pct: float | None = None,
        trade_size_usd: float | None = None,
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
                # No heuristic spread gate — candidate paths are passed
                # through for exact integer accounting (on-chain grid search
                # sizes the optimal input and the gas-floor gate decides).
                if path:
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


# ===========================================================================
# Phase 4: Balancer V2 flashloan client
# ===========================================================================
class FlashloanExecutor:
    """Builds, simulates, and broadcasts flashloan arbitrage via our contract."""

    def __init__(
        self,
        rpc_manager: RPCManager,
        vault_address: str = BALANCER_VAULT,
        executor_address: str = "",
        weth_address: str = WETH_ADDRESS,
        dry_run: bool = True,
        min_net_profit_usd: float = 0.50,
    ) -> None:
        self.rpc = rpc_manager
        self.vault = vault_address
        self.executor = executor_address
        self.weth = weth_address
        self.dry_run = dry_run
        self.min_net_profit_usd = min_net_profit_usd
        self._compute_selectors()

    def _compute_selectors(self) -> None:

        self.flashloan_sel = keccak(
            text="flashLoan(address,address[],uint256[],bytes)"
        )[:4].hex()
        self.execute_sel = keccak(
            text="executeArbitrage(address[],address[],uint24[],bool[],uint256)"
        )[:4].hex()

    def _encode_user_data(
        self,
        path: list[str],
        routers: list[str],
        fee_tiers: list[int] | None = None,
        is_v3: list[bool] | None = None,
    ) -> str:

        if fee_tiers is None:
            fee_tiers = [0] * len(path)
        if is_v3 is None:
            is_v3 = [False] * len(path)
        return abi_encode(
            ["address[]", "address[]", "uint24[]", "bool[]"],
            [path, routers, fee_tiers, is_v3],
        ).hex()

    def build_vault_call(
        self,
        amount_in: int,
        path: list[str],
        routers: list[str],
        fee_tiers: list[int] | None = None,
        is_v3: list[bool] | None = None,
    ) -> str:
        """eth_call payload: Vault.flashLoan(recipient, [WETH], [amount], userData)."""

        user_data = self._encode_user_data(path, routers, fee_tiers, is_v3)
        params = abi_encode(
            ["address", "address[]", "uint256[]", "bytes"],
            [self.executor, [self.weth], [amount_in], "0x" + user_data],
        ).hex()
        return "0x" + self.flashloan_sel + params

    def build_execute_calldata(
        self,
        amount_in: int,
        path: list[str],
        routers: list[str],
        fee_tiers: list[int] | None = None,
        is_v3: list[bool] | None = None,
    ) -> str:

        if fee_tiers is None:
            fee_tiers = [0] * len(path)
        if is_v3 is None:
            is_v3 = [False] * len(path)
        params = abi_encode(
            ["address[]", "address[]", "uint24[]", "bool[]", "uint256"],
            [path, routers, fee_tiers, is_v3, amount_in],
        ).hex()
        return "0x" + self.execute_sel + params

    async def simulate(
        self,
        amount_in: int,
        path: list[str],
        routers: list[str],
        fee_tiers: list[int] | None = None,
        is_v3: list[bool] | None = None,
    ) -> tuple[bool, str]:
        """Zero-gas eth_call against the Vault's flashLoan entrypoint."""
        if not self.executor:
            return False, "flashloan executor not deployed"
        try:
            data = self.build_vault_call(
                amount_in, path, routers, fee_tiers, is_v3
            )
            result = await self.rpc.call_contract(self.vault, data)
            if result is None or result == "0x" or result.startswith("0x"):
                return True, ""
            return False, "empty simulation result"
        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ["revert", "honeypot", "paused"]):
                return False, f"simulation revert: {e}"
            return False, f"simulation error: {e}"

    async def execute(
        self,
        amount_in: int,
        path: list[str],
        routers: list[str],
        live_executor=None,
        fee_tiers: list[int] | None = None,
        is_v3: list[bool] | None = None,
    ) -> tuple[bool, str]:
        data = self.build_execute_calldata(
            amount_in, path, routers, fee_tiers, is_v3
        )
        if self.dry_run:
            logger.info(
                "PAPER FLASHLOAN: borrow=%d wei hops=%d", amount_in, len(routers)
            )
            return True, "0x" + "0" * 64

        if live_executor is None:
            return False, "live execution not configured"

        result = await live_executor.execute_calldata(self.executor, data)
        if result.status == "SUBMITTED":
            return True, result.tx_hash
        return False, result.error or "execution failed"


# ===========================================================================
# Phase 2+3: N-hop graph arbitrage engine
# ===========================================================================
class GraphArbEngine:
    """Consumes Sync events, maintains the token graph, and trades cycles."""

    def __init__(
        self,
        rpc_manager: RPCManager,
        weth_oracle: WETHPriceOracle,
        vault_address: str = BALANCER_VAULT,
        executor_address: str = "",
        weth_address: str = WETH_ADDRESS,
        dry_run: bool = True,
        gas_price_buffer_wei: int = 0,
        live_executor=None,
        on_result: Callable[..., Awaitable[None]] | None = None,
        on_opportunity: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self.rpc = rpc_manager
        self.oracle = weth_oracle
        self.weth = weth_address
        self.dry_run = dry_run
        # Absolute integer gas buffer (wei) for EVM-output accounting. The
        # execution gate is net_profit_wei > gas_price_buffer_wei.
        self.gas_price_buffer_wei = gas_price_buffer_wei
        self.live_executor = live_executor
        self.on_result = on_result
        self.on_opportunity = on_opportunity
        # Flashloan capital is unbounded (Balancer V2 0% loan) — no hardcoded
        # cap. The on-chain grid search sizes the true optimum on deep
        # liquidity imbalances (see _evaluate_cycle).

        self.flashloan = FlashloanExecutor(
            rpc_manager,
            vault_address,
            executor_address,
            weth_address,
            dry_run,
            min_net_profit_usd=0.0,
        )
        # On-chain quoting via QuoterV2 + Multicall3 replaces local tick math.
        self.quoter = OnChainQuoter(rpc_manager)
        self.graph = self._build_graph()
        self._seen_cycles: set[tuple[str, ...]] = set()
        self._weth_price: float | None = None

    def _format_path(
        self, tokens: list[str], dexes: list[str], fee_tiers: list[int]
    ) -> str:
        """Render the cycle as a human-readable hop chain, e.g.:

        ``WETH -> (UniV3 500) -> ARB -> (SushiV2) -> USDC -> (CamelotV3 3000) -> WETH``
        """
        parts: list[str] = [_token_label(tokens[0])]
        for i, dex in enumerate(dexes):
            label = DEX_LABELS.get(dex, dex)
            if fee_tiers[i]:
                parts.append(f"({label} {fee_tiers[i]})")
            else:
                parts.append(f"({label})")
            parts.append(_token_label(tokens[i + 1]))
        return " -> ".join(parts)

    def _build_graph(self) -> DirectedGraph:
        import json
        from pathlib import Path

        edges: list[PoolEdge] = []
        v3_edges: list[V3PoolEdge] = []
        raw = json.loads(Path("config/whitelist.json").read_text())
        for entry in raw:
            dex = entry.get("dex", "")
            if dex in ("uniswap_v3", "camelot_v3"):
                v3_edges.append(
                    V3PoolEdge(
                        pool_address=entry["pair_address"].lower(),
                        token0=entry["token0"].lower(),
                        token1=entry["token1"].lower(),
                        dex=dex,
                        fee_bps=int(entry.get("fee_tier", 3000)),
                    )
                )
            else:
                edges.append(
                    PoolEdge(
                        pair_address=entry["pair_address"].lower(),
                        token0=entry["token0"].lower(),
                        token1=entry["token1"].lower(),
                        dex=dex,
                    )
                )
        g = DirectedGraph(edges, v3_edges)
        logger.info(
            "Graph built: %d nodes, %d V2 + %d V3 pools",
            len(g.nodes),
            len(g.edges),
            len(g.v3_edges),
        )
        return g

    async def process_sync(self, event) -> None:
        self.graph.update_reserves(
            event.pair_address, event.reserve0, event.reserve1
        )
        await self._scan_cycles()

    async def process_v3_state(self, event: V3StateEvent) -> None:
        self.graph.update_v3_state(
            event.pool_address, event.sqrt_price_x96, event.tick, event.liquidity
        )
        await self._scan_cycles()

    async def _scan_cycles(self) -> None:
        weth_price = await self._get_weth_price()
        if weth_price is None:
            return
        self._weth_price = weth_price

        cycle = self.graph.find_arbitrage_cycle(self.weth)
        if cycle is None:
            return

        key = tuple(cycle.pools)
        if key in self._seen_cycles:
            return
        self._seen_cycles.add(key)

        await self._evaluate_cycle(cycle, weth_price)

    async def _evaluate_cycle(self, cycle: ArbCycle, weth_price: float) -> None:
        # Flashloan capital is unbounded; the only execution gate is
        # net_profit_wei > gas_price_buffer_wei on the on-chain quote.
        gas_wei = self.gas_price_buffer_wei

        from web3 import Web3

        path = [Web3.to_checksum_address(t) for t in cycle.tokens]
        routers = [DEX_ROUTERS.get(d, "") for d in cycle.dexes]
        if any(r == "" for r in routers):
            logger.warning("CYCLE: missing router for dex %s", cycle.dexes)
            return

        # Per-hop V3 descriptors: fee tier (bps) + whether the hop is V3.
        # V2 hops carry a 0.30% (3000 bps) fee representation for encoding.
        fee_tiers = []
        for i, is_v3_hop in enumerate(cycle.is_v3):
            if is_v3_hop:
                edge = self.graph.v3_edges.get(cycle.pools[i])
                fee_tiers.append(int(edge.fee_bps) if edge else 3000)
            else:
                fee_tiers.append(0)
        is_v3_flags = list(cycle.is_v3)

        # On-chain quote the whole route across the size grid and pick the
        # size that maximises net WETH profit. QuoterV2 prices the path via
        # the EVM, so no local tick math is required.
        amount_in, out, net_wei, grid_matrix, latency_ms = await self.quoter.quote_best_size(
            cycle.tokens, fee_tiers
        )
        if amount_in == 0:
            logger.debug("CYCLE: no on-chain quote produced a positive size — skip")
            return

        # Exact integer accounting: net profit (wei) must clear the estimated
        # gas cost (wei) before we broadcast. The FlashloanExecutor covers
        # capital, so only gas is the execution-floor constraint.
        estimated_gas_cost_wei = gas_wei
        net_profit_wei = net_wei

        # Build the structured hop path string: WETH -> (UniV3 500) -> ...
        hop_labels = self._format_path(cycle.tokens, cycle.dexes, fee_tiers)
        grid_str = " ".join(
            f"{s}:{('REVERT' if v == 'REVERT' else f'{v:.4f}')}"
            for s, v in grid_matrix.items()
        )

        if net_profit_wei > estimated_gas_cost_wei:
            action = "EXECUTE"
        else:
            action = "DROP"
            logger.info(
                "CYCLE EVAL | Path: %s\n"
                "  Quoter RPC: %.2f ms | Hops: %d\n"
                "  Grid Results: {%s}\n"
                "  EV Analysis | Gross: %d wei | Gas Cost: %d wei | "
                "Net EV: %d wei | Action: %s",
                hop_labels,
                latency_ms,
                cycle.hop_count,
                grid_str,
                out,
                estimated_gas_cost_wei,
                net_profit_wei,
                action,
            )
            return

        net_usd = (net_wei / 1e18) * weth_price
        gross_spread_pct = ((out - amount_in) / amount_in) * 100
        trade_size_usd = (amount_in / 1e18) * weth_price

        logger.info(
            "CYCLE EVAL | Path: %s\n"
            "  Quoter RPC: %.2f ms | Hops: %d\n"
            "  Grid Results: {%s}\n"
            "  EV Analysis | Gross: %d wei | Gas Cost: %d wei | "
            "Net EV: %d wei | Action: %s",
            hop_labels,
            latency_ms,
            cycle.hop_count,
            grid_str,
            out,
            estimated_gas_cost_wei,
            net_profit_wei,
            action,
        )

        if self.on_opportunity:
            await self.on_opportunity(
                token_address=cycle.tokens[1],
                dex=",".join(cycle.dexes),
                spread_pct=gross_spread_pct,
                net_profit=net_usd,
                trade_size=trade_size_usd,
                stage="GRAPH_MATH",
            )

        success, reason = await self.flashloan.simulate(
            amount_in, path, routers, fee_tiers, is_v3_flags
        )
        if not success:
            logger.warning("GRAPH SIM FAIL: %s", reason)
            return

        logger.info("GRAPH SIM PASS: %s net=$%.4f", cycle.tokens[1][:10], net_usd)

        ok, tx_hash = await self.flashloan.execute(
            amount_in, path, routers, self.live_executor, fee_tiers, is_v3_flags
        )

        state = ExecutionState(
            trade_id=f"graph_{cycle.tokens[1][:8]}_{int(time.time()*1000)}",
            token_address=cycle.tokens[1],
            token_symbol=cycle.tokens[1][:10],
            dex_name=cycle.dexes[0],
            weth_to_exotic_pool=cycle.pools[0],
            exotic_to_quote_pool=cycle.pools[1] if cycle.hop_count > 1 else "",
            quote_to_weth_pool=cycle.pools[-1],
            input_weth_wei=amount_in,
            output_weth_wei=out,
            gross_spread_pct=gross_spread_pct,
            trade_size_usd=trade_size_usd,
            gas_overhead_usd=self.gas_price_buffer_wei / 1e18,
            net_profit_usd=net_usd,
            status="EXECUTED" if (self.dry_run or ok) else "ABORTED",
            abort_reason="" if (self.dry_run or ok) else f"exec failed: {tx_hash}",
            audit_is_safe=True,
            audit_threats=[],
        )

        if self.on_result:
            await self.on_result(state)

    async def _get_weth_price(self) -> float | None:
        try:
            return await self.oracle.get_weth_price()
        except ValueError:
            return None


# ===========================================================================
# Orchestrating sniper
# ===========================================================================
class ProcessBSniper:
    def __init__(
        self,
        cleared_db: ClearedTokensDB,
        rpc_manager: RPCManager,
        gas_price_buffer_wei: int = 0,
        dry_run: bool = True,
        live_executor=None,
        llm_api_key: str = "",
        llm_model: str = "llama-3.3-70b-versatile",
        on_opportunity: Callable[..., Awaitable[None]] | None = None,
        # Graph-mode wiring
        sync_queue: asyncio.Queue | None = None,
        vault_address: str = BALANCER_VAULT,
        executor_address: str = "",
        weth_address: str = WETH_ADDRESS,
        graph_mode: bool = False,
    ) -> None:
        self.oracle = WETHPriceOracle(rpc_manager)
        self.scanner = TriangularScanner(rpc_manager, cleared_db, self.oracle)
        self.simulator = Simulator(rpc_manager)
        self.gas_price_buffer_wei = gas_price_buffer_wei
        self.dry_run = dry_run
        self.live_executor = live_executor
        self.on_opportunity = on_opportunity

        self.llm_auditor = (
            LLMSecurityAuditor(llm_api_key, llm_model) if llm_api_key else None
        )

        self._running = False
        self._results: list[ExecutionState] = []
        self._weth_price: float | None = None

        # Grid of input sizes (wei) fed to the on-chain Multicall3 grid search.
        # 0.1 WETH, 0.5 WETH, 1.0 WETH, 5.0 WETH, 10.0 WETH
        self.grid_sizes_wei = [
            10**17,
            5 * 10**17,
            10**18,
            5 * 10**18,
            10**19,
        ]

        self.sync_queue = sync_queue
        self.graph_mode = graph_mode
        self.graph_engine: GraphArbEngine | None = None
        if graph_mode and sync_queue is not None:
            self.graph_engine = GraphArbEngine(
                rpc_manager=rpc_manager,
                weth_oracle=self.oracle,
                vault_address=vault_address,
                executor_address=executor_address,
                weth_address=weth_address,
                dry_run=dry_run,
                gas_price_buffer_wei=gas_price_buffer_wei,
                live_executor=live_executor,
                on_result=self._append_result,
                on_opportunity=on_opportunity,
            )

    # -- graph path --------------------------------------------------------
    async def run(self, scan_interval: float = 1.0) -> None:
        self._running = True
        if self.graph_mode and self.graph_engine is not None:
            logger.info("Process B: Graph Sniper started (WSS-driven)")
            while self._running:
                try:
                    event = await self.sync_queue.get()
                    if isinstance(event, V3StateEvent):
                        await self.graph_engine.process_v3_state(event)
                    else:
                        await self.graph_engine.process_sync(event)
                except Exception as e:
                    logger.error("Graph sniper cycle failed: %s", e)
            return

        logger.info("Process B: Triangular Sniper started (interval=%.1fs)", scan_interval)
        while self._running:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error("Sniper scan cycle failed: %s", e)
            await asyncio.sleep(scan_interval)

    # -- triangular path ---------------------------------------------------
    async def _scan_cycle(self) -> None:
        paths = await self.scanner.scan()

        self._weth_price = await self.scanner._get_weth_price()
        if self._weth_price is None:
            return

        for path in paths:
            state = await self._evaluate_path(path)
            if state:
                self._results.append(state)

    async def _evaluate_path(self, path: TriangularPath) -> ExecutionState | None:
        trade_id = f"{path.token_address[:10]}_{int(time.time() * 1000)}"

        # Legacy triangular path: trade size defaults to the grid midpoint;
        # gas is the absolute integer buffer (wei) for exact accounting.
        trade_size_usd = (self.grid_sizes_wei[2] / 1e18) * (self._weth_price or 0.0)
        gas_wei = self.gas_price_buffer_wei

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
            trade_size_usd=trade_size_usd,
            gas_overhead_usd=gas_wei / 1e18,
            net_profit_usd=0.0,
            quote_symbol=path.quote_symbol,
            exotic_amount_wei=path.exotic_amount_wei,
            quote_amount_raw=path.quote_amount_raw,
        )

        if path.output_weth_wei <= path.input_weth_wei + gas_wei:
            net_profit, _ = compute_net_profit(
                path.gross_spread_pct, trade_size_usd, gas_wei / 1e18
            )
            state.net_profit_usd = net_profit
            state.status = "ABORTED"
            state.abort_reason = (
                f"math gate: output {path.output_weth_wei} <= "
                f"input+gas {path.input_weth_wei + gas_wei}"
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
                path.gross_spread_pct, trade_size_usd, gas_wei / 1e18
            )
            await self.on_opportunity(
                token_address=path.token_address,
                dex=path.dex_name,
                spread_pct=path.gross_spread_pct,
                net_profit=net_profit_math,
                trade_size=trade_size_usd,
                stage="MATH",
            )

        sim_success, sim_reason, sim_output = (
            await self.simulator.simulate_triangular(
                path, trade_size_usd, self._weth_price
            )
        )

        if not sim_success:
            state.eth_call_success = False
            state.eth_call_revert_reason = sim_reason
            state.status = "ABORTED"
            state.abort_reason = f"simulation failed: {sim_reason}"
            return state

        state.eth_call_success = True
        state.output_weth_wei = sim_output
        state.output_weth_eth = sim_output / 1e18

        net_profit, is_profitable = compute_net_profit(
            path.gross_spread_pct, trade_size_usd, gas_wei / 1e18
        )
        state.net_profit_usd = net_profit

        if not is_profitable:
            state.status = "ABORTED"
            state.abort_reason = f"net profit ${net_profit:.4f} below floor"
            return state

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
                    return state
            else:
                state.audit_is_safe = True
                state.audit_threats = []
        else:
            state.audit_is_safe = True
            state.audit_threats = []

        state.status = "AUTHORIZED"

        if self.dry_run:
            state.status = "EXECUTED"
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
                value_eth=state.trade_size_usd / self._weth_price,
            )

            if result.status == "SUBMITTED":
                state.status = "EXECUTED"
                state.tx_hash = result.tx_hash
            else:
                state.status = "ABORTED"
                state.abort_reason = result.error or "execution failed"

        except Exception as e:
            state.status = "ABORTED"
            state.abort_reason = f"execution error: {e}"

        return state

    async def _append_result(self, state: ExecutionState) -> None:
        self._results.append(state)

    def get_results(self) -> list[ExecutionState]:
        return self._results

    def stop(self) -> None:
        self._running = False
        logger.info("Process B: Sniper stopped")
