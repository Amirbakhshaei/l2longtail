"""
Single-DEX Triangular Arbitrage Math Engine.

Implements WETH -> EXOTIC -> USDC -> WETH constant product routing
on a single originating DEX. No cross-DEX spatial arbitrage.

All math is local x*y=k with 0.3% LP fee per hop.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

V2_FEE = 0.997
LP_FEE_BPS = 30
INF = float("inf")


def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int, fee_tier: int = 3) -> int:
    """
    V2 constant-product output calculation with fee deduction.

    amount_in_after_fee = amount_in * (1000 - fee_tier) / 1000
    numerator = amount_in_after_fee * reserve_out
    denominator = reserve_in + amount_in_after_fee
    return numerator // denominator

    Returns 0 if reserves are zero or amount_in is zero.
    """
    if reserve_in == 0 or reserve_out == 0 or amount_in == 0:
        return 0
    amount_in_after_fee = amount_in * (1000 - fee_tier)
    numerator = amount_in_after_fee * reserve_out
    denominator = (reserve_in * 1000) + amount_in_after_fee
    return numerator // denominator


def calculate_triangular_route(
    input_weth: int,
    res_weth_exotic: tuple[int, int],
    res_exotic_usdc: tuple[int, int],
    res_usdc_weth: tuple[int, int],
) -> int:
    """
    3-hop triangular arbitrage: WETH -> EXOTIC -> USDC -> WETH.

    All reserves are (reserve0, reserve1) as returned by getReserves().
    The caller must ensure the tuple ordering matches the direction of each hop:
      - res_weth_exotic: (weth_reserve, exotic_reserve) — WETH in, EXOTIC out
      - res_exotic_usdc: (exotic_reserve, usdc_reserve)  — EXOTIC in, USDC out
      - res_usdc_weth:  (usdc_reserve, weth_reserve)    — USDC in, WETH out

    Returns final_weth_out. Returns 0 if any hop fails due to insufficient liquidity.
    """
    hop1 = get_amount_out(input_weth, res_weth_exotic[0], res_weth_exotic[1])
    if hop1 == 0:
        return 0

    hop2 = get_amount_out(hop1, res_exotic_usdc[0], res_exotic_usdc[1])
    if hop2 == 0:
        return 0

    hop3 = get_amount_out(hop2, res_usdc_weth[0], res_usdc_weth[1])
    return hop3


def compute_v2_output(
    reserve_in: int,
    reserve_out: int,
    amount_in: int,
    fee: float = V2_FEE,
) -> int:
    if reserve_in == 0 or reserve_out == 0 or amount_in == 0:
        return 0
    amount_in_with_fee = int(amount_in * fee)
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in + amount_in_with_fee
    return numerator // denominator


def compute_v2_price(
    reserve_in: int,
    reserve_out: int,
    decimals_in: int = 18,
    decimals_out: int = 18,
) -> float:
    if reserve_in == 0:
        return 0.0
    adjusted_in = reserve_in / (10 ** decimals_in)
    adjusted_out = reserve_out / (10 ** decimals_out)
    return adjusted_out / adjusted_in


@dataclass
class PoolInfo:
    """Reserve data with explicit token position mapping."""

    weth_is_r0: bool
    reserves: tuple[int, int]
    pair_address: str = ""

    @property
    def weth_reserve(self) -> int:
        return self.reserves[0] if self.weth_is_r0 else self.reserves[1]

    @property
    def exotic_reserve(self) -> int:
        return self.reserves[1] if self.weth_is_r0 else self.reserves[0]


@dataclass
class QuotePoolInfo:
    """Pool info for a quote token (USDC/USDT) paired with the exotic."""

    quote_is_r0: bool
    reserves: tuple[int, int]
    pair_address: str = ""
    quote_symbol: str = ""

    @property
    def quote_reserve(self) -> int:
        return self.reserves[0] if self.quote_is_r0 else self.reserves[1]

    @property
    def exotic_reserve(self) -> int:
        return self.reserves[1] if self.quote_is_r0 else self.reserves[0]


@dataclass
class TriangularPath:
    """Result of a single-DEX triangular arbitrage calculation."""

    token_address: str
    token_symbol: str
    dex_name: str

    weth_to_exotic_pool: str
    exotic_to_quote_pool: str
    quote_to_weth_pool: str

    input_weth_wei: int
    output_weth_wei: int
    gross_profit_wei: int

    input_weth_eth: float
    output_weth_eth: float
    gross_profit_eth: float
    gross_spread_pct: float

    exotic_amount_wei: int
    quote_amount_raw: int

    quote_symbol: str
    quote_to_weth_pair: str


def find_triangular_path(
    token_address: str,
    token_symbol: str,
    dex_name: str,
    weth_pool: PoolInfo,
    quote_pools: dict[str, QuotePoolInfo],
    weth_usdc_pool: PoolInfo | None = None,
    input_weth_wei: int = 10**18,
) -> list[TriangularPath]:
    """
    Calculate single-DEX triangular arbitrage: WETH -> EXOTIC -> QUOTE -> WETH.

    All three hops must occur on the SAME originating DEX.

    Args:
        token_address: The exotic token address.
        token_symbol: Human-readable symbol.
        dex_name: The originating DEX (e.g., "uniswap_v2").
        weth_pool: The EXOTIC/WETH pool on this DEX.
        quote_pools: Dict of quote symbol -> QuotePoolInfo for EXOTIC/USDC, EXOTIC/USDT on this DEX.
        weth_usdc_pool: The WETH/USDC pool on this DEX for the final hop.
        input_weth_wei: Trade size in WETH wei (default 1 ETH = $10 notional).

    Returns:
        List of profitable paths sorted by spread descending.
    """
    if not quote_pools:
        return []

    paths: list[TriangularPath] = []

    for quote_sym, quote_pool in quote_pools.items():
        exotic_amount = compute_v2_output(
            reserve_in=weth_pool.weth_reserve,
            reserve_out=weth_pool.exotic_reserve,
            amount_in=input_weth_wei,
        )
        if exotic_amount == 0:
            continue

        quote_amount = compute_v2_output(
            reserve_in=quote_pool.exotic_reserve,
            reserve_out=quote_pool.quote_reserve,
            amount_in=exotic_amount,
        )
        if quote_amount == 0:
            continue

        if weth_usdc_pool is None:
            continue

        if weth_usdc_pool.weth_is_r0:
            final_weth_reserve = weth_usdc_pool.weth_reserve
            final_usdc_reserve = weth_usdc_pool.exotic_reserve
        else:
            final_weth_reserve = weth_usdc_pool.exotic_reserve
            final_usdc_reserve = weth_usdc_pool.weth_reserve

        output_weth = compute_v2_output(
            reserve_in=final_usdc_reserve,
            reserve_out=final_weth_reserve,
            amount_in=quote_amount,
        )
        if output_weth == 0:
            continue

        gross_profit_wei = output_weth - input_weth_wei
        gross_spread_pct = (gross_profit_wei / input_weth_wei) * 100

        input_eth = input_weth_wei / 1e18
        output_eth = output_weth / 1e18
        profit_eth = gross_profit_wei / 1e18

        paths.append(
            TriangularPath(
                token_address=token_address,
                token_symbol=token_symbol,
                dex_name=dex_name,
                weth_to_exotic_pool=weth_pool.pair_address,
                exotic_to_quote_pool=quote_pool.pair_address,
                quote_to_weth_pool=weth_usdc_pool.pair_address,
                input_weth_wei=input_weth_wei,
                output_weth_wei=output_weth,
                gross_profit_wei=gross_profit_wei,
                input_weth_eth=input_eth,
                output_weth_eth=output_eth,
                gross_profit_eth=profit_eth,
                gross_spread_pct=gross_spread_pct,
                exotic_amount_wei=exotic_amount,
                quote_amount_raw=quote_amount,
                quote_symbol=quote_sym,
                quote_to_weth_pair=weth_usdc_pool.pair_address,
            )
        )

    return sorted(paths, key=lambda p: p.gross_spread_pct, reverse=True)


@dataclass
class ExecutionState:
    """State object for the execution pipeline."""

    trade_id: str
    token_address: str
    token_symbol: str
    dex_name: str

    weth_to_exotic_pool: str
    exotic_to_quote_pool: str
    quote_to_weth_pool: str

    input_weth_wei: int
    output_weth_wei: int
    gross_spread_pct: float
    trade_size_usd: float
    gas_overhead_usd: float
    net_profit_usd: float

    eth_call_success: bool = False
    eth_call_revert_reason: str = ""

    audit_is_safe: bool = False
    audit_threats: list[str] | None = None

    status: str = "PENDING"
    abort_reason: str = ""
    tx_hash: str = ""

    quote_symbol: str = ""
    exotic_amount_wei: int = 0
    quote_amount_raw: int = 0


def compute_net_profit(
    spread_pct: float,
    trade_size_usd: float,
    gas_usd: float = 0.02,
    min_profit_usd: float = 0.50,
) -> tuple[float, bool]:
    gross_profit = (spread_pct / 100) * trade_size_usd
    net_profit = gross_profit - gas_usd
    return net_profit, net_profit >= min_profit_usd


# ===========================================================================
# Phase 2 + 3: N-Hop Graph Routing (Bellman-Ford) & Optimal Input Sizing
# ===========================================================================


@dataclass
class PoolEdge:
    """A bidirectional liquidity edge between two tokens (one V2 pool)."""

    pair_address: str
    token0: str
    token1: str
    dex: str
    reserve0: int = 0
    reserve1: int = 0

    def reserves_from(self, from_token: str) -> tuple[int, int]:
        """Return (reserve_in, reserve_out) when swapping from_token -> other."""
        if from_token.lower() == self.token0.lower():
            return self.reserve0, self.reserve1
        return self.reserve1, self.reserve0


@dataclass
class ArbCycle:
    """A negative-weight cycle (arbitrage loop) discovered by Bellman-Ford."""

    tokens: list[str]  # length k+1, tokens[0] == tokens[-1]
    pools: list[str]   # length k, pools[i] connects tokens[i] -> tokens[i+1]
    dexes: list[str]   # length k, dex venue per hop

    @property
    def hop_count(self) -> int:
        return len(self.pools)

    def directed_hops(self, graph: "DirectedGraph") -> list[tuple[int, int]]:
        """Reserve tuples (reserve_in, reserve_out) for each hop in order."""
        hops: list[tuple[int, int]] = []
        for i, pool in enumerate(self.pools):
            edge = graph.edges[pool]
            hops.append(edge.reserves_from(self.tokens[i]))
        return hops


class DirectedGraph:
    """
    Token graph over whitelisted V2 pools.

    Nodes = tokens. Edges = pools (bidirectional). Edge weights are
    ``-log(rate_after_0.3%_fee)`` where rate is the spot output-per-input of
    the constant-product curve. A negative-weight cycle therefore implies a
    profitable arbitrage loop whose product of execution rates exceeds 1.

    On every Sync event, ``update_reserves`` mutates the affected edge, which
    immediately changes the weights used by the next ``find_arbitrage_cycle``.
    """

    def __init__(self, pools: list[PoolEdge] | None = None) -> None:
        self.nodes: set[str] = set()
        self.edges: dict[str, PoolEdge] = {}
        self.adj: dict[str, list[str]] = {}
        if pools:
            for p in pools:
                self.add_pool(p)

    def add_pool(self, pool: PoolEdge) -> None:
        self.edges[pool.pair_address.lower()] = pool
        self.nodes.add(pool.token0)
        self.nodes.add(pool.token1)
        self.adj.setdefault(pool.token0, []).append(pool.pair_address.lower())
        self.adj.setdefault(pool.token1, []).append(pool.pair_address.lower())

    def update_reserves(
        self, pair_address: str, reserve0: int, reserve1: int
    ) -> None:
        edge = self.edges.get(pair_address.lower())
        if edge is None:
            return
        edge.reserve0 = reserve0
        edge.reserve1 = reserve1

    def _weight(self, edge_key: str, from_token: str) -> float:
        edge = self.edges[edge_key]
        reserve_in, reserve_out = edge.reserves_from(from_token)
        if reserve_in == 0 or reserve_out == 0:
            return INF
        rate = (reserve_out * V2_FEE) / reserve_in
        if rate <= 0:
            return INF
        return -math.log(rate)

    def find_arbitrage_cycle(self, start_token: str) -> ArbCycle | None:
        """
        Bellman-Ford from ``start_token``. Detect a negative-weight cycle and
        reconstruct the exact token/pool path. Returns None if no cycle exists.
        """
        if start_token not in self.nodes:
            return None

        nodes = sorted(self.nodes)
        idx = {n: i for i, n in enumerate(nodes)}
        N = len(nodes)
        start = idx[start_token]

        dist = [INF] * N
        pred: list[int | None] = [None] * N
        pred_edge: list[str | None] = [None] * N
        dist[start] = 0.0

        def relax_all() -> bool:
            changed = False
            for key, edge in self.edges.items():
                # direction token0 -> token1
                u, v = idx[edge.token0], idx[edge.token1]
                w = self._weight(key, edge.token0)
                if dist[u] + w < dist[v] - 1e-15:
                    dist[v] = dist[u] + w
                    pred[v] = u
                    pred_edge[v] = key
                    changed = True
                # direction token1 -> token0
                u2, v2 = v, u
                w2 = self._weight(key, edge.token1)
                if dist[u2] + w2 < dist[v2] - 1e-15:
                    dist[v2] = dist[u2] + w2
                    pred[v2] = u2
                    pred_edge[v2] = key
                    changed = True
            return changed

        for _ in range(N - 1):
            if not relax_all():
                break

        # Nth pass: locate a node still relaxable => on a negative cycle
        cycle_node: int | None = None
        for key, edge in self.edges.items():
            u, v = idx[edge.token0], idx[edge.token1]
            if dist[u] + self._weight(key, edge.token0) < dist[v] - 1e-15:
                cycle_node = v
                break
            if dist[v] + self._weight(key, edge.token1) < dist[u] - 1e-15:
                cycle_node = u
                break
        if cycle_node is None:
            return None

        # Walk pred N times to land inside the cycle
        x = cycle_node
        for _ in range(N):
            x = pred[x]  # type: ignore[assignment]

        # Reconstruct the cycle (list of edges along pred)
        cur = x
        path_tokens: list[str] = [nodes[cur]]
        path_pools: list[str] = []
        guard = 0
        while guard <= N:
            e = pred_edge[cur]
            prev = pred[cur]
            if e is None or prev is None:
                break
            path_pools.append(e)
            path_tokens.append(nodes[prev])
            cur = prev
            guard += 1
            if cur == x:
                break
        path_tokens.reverse()
        path_pools.reverse()

        if len(path_pools) < 2:
            return None

        dexes = [self.edges[p].dex for p in path_pools]

        cycle = ArbCycle(tokens=path_tokens, pools=path_pools, dexes=dexes)

        # Rotate so the loop begins (and ends) at the requested start token.
        # Required because the flashloan borrows/lends this asset.
        return self._rotate_to_start(cycle, start_token)

    def _rotate_to_start(self, cycle: ArbCycle, start_token: str) -> ArbCycle:
        start_lower = start_token.lower()
        if cycle.tokens[0].lower() == start_lower:
            return cycle
        try:
            idx = next(
                i
                for i, t in enumerate(cycle.tokens)
                if t.lower() == start_lower
            )
        except StopIteration:
            return cycle
        tokens = cycle.tokens[idx:-1] + cycle.tokens[: idx + 1]
        pools = cycle.pools[idx:] + cycle.pools[:idx]
        dexes = cycle.dexes[idx:] + cycle.dexes[:idx]
        return ArbCycle(tokens=tokens, pools=pools, dexes=dexes)


def get_amount_out_multi_hop(
    directed_hops: list[tuple[int, int]],
    amount_in: int,
) -> int:
    """
    Chain constant-product swaps across an ordered list of (reserve_in,
    reserve_out) tuples. Applies the 0.3% V2 fee per hop. Returns the final
    output amount (0 if any hop fails).
    """
    out = amount_in
    for reserve_in, reserve_out in directed_hops:
        out = get_amount_out(out, reserve_in, reserve_out)
        if out == 0:
            return 0
    return out


def calculate_optimal_input_size(
    directed_hops: list[tuple[int, int]],
    gas_cost_wei: int,
    max_input_wei: int,
    iterations: int = 100,
) -> int:
    """
    Ternary search for the exact ``amount_in`` that maximises net profit:

        f(x) = get_amount_out_multi_hop(x) - x - gas_cost_wei

    The objective is unimodal (concave) on x*y=k curves, so ternary search
    converges to the local maximum. Bounds are [1 wei, max_input_wei].

    Returns the optimal ``amount_in`` in wei, or 0 if max profit <= 0.
    """

    def objective(x: int) -> int:
        return get_amount_out_multi_hop(directed_hops, x) - x - gas_cost_wei

    lo, hi = 1, max(max_input_wei, 1)
    for _ in range(iterations):
        if hi - lo <= 3:
            break
        m1 = lo + (hi - lo) // 3
        m2 = hi - (hi - lo) // 3
        if objective(m1) < objective(m2):
            lo = m1
        else:
            hi = m2

    # Evaluate the narrowed window and pick the global maximum
    best_x = lo
    best_f = objective(lo)
    for x in range(lo + 1, hi + 1):
        f = objective(x)
        if f > best_f:
            best_f = f
            best_x = x

    if best_f <= 0:
        return 0
    return best_x
