"""
Single-DEX Triangular Arbitrage Math Engine.

Implements WETH -> EXOTIC -> USDC -> WETH constant product routing
on a single originating DEX. No cross-DEX spatial arbitrage.

All math is local x*y=k with 0.3% LP fee per hop.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

V2_FEE = 0.997
LP_FEE_BPS = 30


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
