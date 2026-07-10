"""
Local price calculation from reserves.

Eliminates getAmountsOut() RPC calls by computing prices locally.
V2: constant product formula x * y = k
V3: concentrated liquidity math using slot0 and liquidity
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

V2_FEE = 0.997


@dataclass
class LocalQuote:
    dex_name: str
    pair_address: str
    amount_out: float
    price_per_token: float
    reserve_in: int
    reserve_out: int


@dataclass
class V3Quote:
    dex_name: str
    pool_address: str
    amount_out: float
    price_per_token: float
    sqrt_price_x96: int
    liquidity: int
    tick: int


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


def compute_v2_spread(
    reserve_a_buy: int,
    reserve_b_buy: int,
    reserve_a_sell: int,
    reserve_b_sell: int,
    amount_in_wei: int = 10**18,
) -> float:
    out_buy = compute_v2_output(reserve_a_buy, reserve_b_buy, amount_in_wei)
    out_sell = compute_v2_output(reserve_b_sell, reserve_a_sell, out_buy)

    if out_buy == 0:
        return 0.0

    spread = (out_sell - amount_in_wei) / amount_in_wei
    return spread * 100


@dataclass
class V3PoolState:
    sqrt_price_x96: int
    liquidity: int
    tick: int
    fee: int


def compute_v3_output(
    pool: V3PoolState,
    amount_in: int,
    zero_for_one: bool,
) -> int:
    if pool.liquidity == 0 or pool.sqrt_price_x96 == 0:
        return 0

    fee_divisor = 1000000
    amount_in_after_fee = amount_in * (fee_divisor - pool.fee) // fee_divisor

    sqrt_price = pool.sqrt_price_x96 / (2**96)

    if zero_for_one:
        amount_out = (
            amount_in_after_fee * pool.liquidity * sqrt_price
        ) // (pool.sqrt_price_x96 + (amount_in_after_fee << 96) // pool.liquidity)
    else:
        amount_out = (
            amount_in_after_fee * pool.liquidity
        ) // (pool.sqrt_price_x96 + (amount_in_after_fee << 96) // pool.liquidity)

    return amount_out


def tick_to_sqrt_price(tick: int) -> float:
    return 1.0001 ** (tick / 2)


def sqrt_price_to_tick(sqrt_price_x96: int) -> int:
    sqrt_price = sqrt_price_x96 / (2**96)
    return int(math.log(sqrt_price, math.sqrt(1.0001)))


def price_to_sqrt_price_x96(price: float) -> int:
    sqrt_price = math.sqrt(price)
    return int(sqrt_price * (2**96))


@dataclass
class PoolInfo:
    """Reserve data with explicit WETH token position mapping."""

    weth_is_r0: bool
    reserves: tuple[int, int]

    @property
    def weth_reserve(self) -> int:
        return self.reserves[0] if self.weth_is_r0 else self.reserves[1]

    @property
    def exotic_reserve(self) -> int:
        return self.reserves[1] if self.weth_is_r0 else self.reserves[0]


@dataclass
class LocalArbitrageOpportunity:
    token_address: str
    token_symbol: str
    buy_dex: str
    buy_pair: str
    sell_dex: str
    sell_pair: str
    buy_price: float
    sell_price: float
    spread_pct: float
    buy_reserves: tuple[int, int]
    sell_reserves: tuple[int, int]


def find_local_spreads(
    token_address: str,
    token_symbol: str,
    pools: dict[str, PoolInfo],
    min_spread_pct: float = 1.0,
) -> list[LocalArbitrageOpportunity]:
    """Detect cross-DEX arbitrage spreads using dynamic WETH token position mapping."""
    dex_names = list(pools.keys())
    opportunities: list[LocalArbitrageOpportunity] = []

    for i in range(len(dex_names)):
        for j in range(len(dex_names)):
            if i == j:
                continue

            buy_dex = dex_names[i]
            sell_dex = dex_names[j]

            buy_pool = pools[buy_dex]
            sell_pool = pools[sell_dex]

            amount_in = 10**18

            buy_amount_out = compute_v2_output(
                reserve_in=buy_pool.weth_reserve,
                reserve_out=buy_pool.exotic_reserve,
                amount_in=amount_in,
            )

            if buy_amount_out == 0:
                continue

            sell_amount_out = compute_v2_output(
                reserve_in=sell_pool.exotic_reserve,
                reserve_out=sell_pool.weth_reserve,
                amount_in=buy_amount_out,
            )

            if sell_amount_out == 0:
                continue

            spread_pct = ((sell_amount_out - amount_in) / amount_in) * 100

            buy_price = (
                buy_pool.weth_reserve / buy_pool.exotic_reserve
                if buy_pool.exotic_reserve > 0
                else 0
            )
            sell_price = (
                sell_pool.weth_reserve / sell_pool.exotic_reserve
                if sell_pool.exotic_reserve > 0
                else 0
            )

            if spread_pct >= min_spread_pct:
                opportunities.append(
                    LocalArbitrageOpportunity(
                        token_address=token_address,
                        token_symbol=token_symbol,
                        buy_dex=buy_dex,
                        buy_pair=f"{buy_dex}_pool",
                        sell_dex=sell_dex,
                        sell_pair=f"{sell_dex}_pool",
                        buy_price=buy_price,
                        sell_price=sell_price,
                        spread_pct=spread_pct,
                        buy_reserves=buy_pool.reserves,
                        sell_reserves=sell_pool.reserves,
                    )
                )

    return sorted(opportunities, key=lambda x: x.spread_pct, reverse=True)


def compute_net_profit(
    spread_pct: float,
    trade_size_usd: float,
    gas_usd: float = 0.02,
    min_profit_usd: float = 0.50,
) -> tuple[float, bool]:
    gross_profit = (spread_pct / 100) * trade_size_usd
    net_profit = gross_profit - gas_usd
    return net_profit, net_profit >= min_profit_usd
