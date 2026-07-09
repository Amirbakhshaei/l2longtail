"""
Uniswap V3 / Camelot V3 concentrated liquidity support.

Handles slot0, liquidity, and tick-based price impact calculations.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SLOT0_SELECTOR = "0x3859248c"
LIQUIDITY_SELECTOR = "0x1a686502"

V3_FEE_TIERS = {
    100: 100,
    500: 500,
    3000: 3000,
    10000: 10000,
}


@dataclass
class Slot0:
    sqrt_price_x96: int
    tick: int
    observation_index: int
    observation_cardinality: int
    fee_protocol: int
    unlocked: bool


@dataclass
class V3PoolState:
    pool_address: str
    token0: str
    token1: str
    fee: int
    slot0: Slot0
    liquidity: int
    tick_spacing: int


@dataclass
class V3QuoteResult:
    amount_out: int
    sqrt_price_x96_after: int
    tick_after: int
    gas_used: int


def sqrt_price_x96_to_price(
    sqrt_price_x96: int,
    decimals_token0: int = 18,
    decimals_token1: int = 18,
) -> float:
    price = (sqrt_price_x96 / (2**96)) ** 2
    return price / (10 ** (decimals_token0 - decimals_token1))


def price_to_sqrt_price_x96(price: float) -> int:
    sqrt_price = math.sqrt(price)
    return int(sqrt_price * (2**96))


def tick_to_sqrt_price(tick: int) -> int:
    ratio = 1.0001 ** tick
    return int(math.sqrt(ratio) * (2**96))


def sqrt_price_to_tick(sqrt_price_x96: int) -> int:
    sqrt_price = sqrt_price_x96 / (2**96)
    return int(math.log(sqrt_price) / math.log(math.sqrt(1.0001)))


def next_tick(tick: int, tick_spacing: int, zero_for_one: bool) -> int:
    if zero_for_one:
        return (tick // tick_spacing) * tick_spacing
    return (tick // tick_spacing + 1) * tick_spacing


def compute_v3_output_amount(
    pool: V3PoolState,
    amount_in: int,
    zero_for_one: bool,
) -> V3QuoteResult:
    if pool.liquidity == 0 or pool.slot0.sqrt_price_x96 == 0:
        return V3QuoteResult(
            amount_out=0,
            sqrt_price_x96_after=pool.slot0.sqrt_price_x96,
            tick_after=pool.slot0.tick,
            gas_used=0,
        )

    fee_divisor = 1000000
    amount_in_after_fee = amount_in * (fee_divisor - pool.fee) // fee_divisor

    sqrt_price = pool.slot0.sqrt_price_x96
    liquidity = pool.liquidity
    tick = pool.slot0.tick

    remaining_in = amount_in_after_fee
    total_out = 0

    max_iterations = 10
    for _ in range(max_iterations):
        if remaining_in <= 0:
            break

        next_tick_val = next_tick(tick, pool.tick_spacing, zero_for_one)

        if zero_for_one:
            sqrt_price_next = tick_to_sqrt_price(next_tick_val)
            amount_out_step = (
                liquidity * (sqrt_price - sqrt_price_next) * remaining_in
            ) // (
                sqrt_price * sqrt_price_next
                + remaining_in * sqrt_price
            )
        else:
            sqrt_price_next = tick_to_sqrt_price(next_tick_val)
            amount_out_step = (
                liquidity * (sqrt_price_next - sqrt_price) * remaining_in
            ) // (
                sqrt_price * sqrt_price_next
                + remaining_in * sqrt_price_next
            )

        if amount_out_step <= 0:
            break

        total_out += amount_out_step
        remaining_in = 0

        tick = next_tick_val
        sqrt_price = sqrt_price_next

    return V3QuoteResult(
        amount_out=total_out,
        sqrt_price_x96_after=sqrt_price,
        tick_after=tick,
        gas_used=0,
    )


def estimate_v3_price_impact(
    pool: V3PoolState,
    amount_in_wei: int,
    zero_for_one: bool,
) -> float:
    if pool.liquidity == 0:
        return 0.0

    price_before = sqrt_price_x96_to_price(pool.slot0.sqrt_price_x96)

    result = compute_v3_output_amount(pool, amount_in_wei, zero_for_one)

    price_after = sqrt_price_x96_to_price(result.sqrt_price_x96_after)

    if zero_for_one:
        price_impact = (price_before - price_after) / price_before
    else:
        price_impact = (price_after - price_before) / price_before

    return price_impact * 100


def find_v3_arbitrage(
    token_address: str,
    token_symbol: str,
    pools: dict[str, V3PoolState],
    min_spread_pct: float = 1.0,
) -> list[dict]:

    dex_names = list(pools.keys())
    opportunities = []

    for i in range(len(dex_names)):
        for j in range(len(dex_names)):
            if i == j:
                continue

            buy_dex = dex_names[i]
            sell_dex = dex_names[j]

            buy_pool = pools[buy_dex]
            sell_pool = pools[sell_dex]

            amount_in = 10**18

            buy_result = compute_v3_output_amount(buy_pool, amount_in, True)
            if buy_result.amount_out == 0:
                continue

            sell_result = compute_v3_output_amount(sell_pool, buy_result.amount_out, False)
            if sell_result.amount_out == 0:
                continue

            spread_pct = ((sell_result.amount_out - amount_in) / amount_in) * 100

            if spread_pct >= min_spread_pct:
                opportunities.append({
                    "token_address": token_address,
                    "token_symbol": token_symbol,
                    "buy_dex": buy_dex,
                    "buy_pool": buy_pool.pool_address,
                    "sell_dex": sell_dex,
                    "sell_pool": sell_pool.pool_address,
                    "spread_pct": spread_pct,
                    "buy_amount_out": buy_result.amount_out,
                    "sell_amount_out": sell_result.amount_out,
                })

    return sorted(opportunities, key=lambda x: x["spread_pct"], reverse=True)


def parse_slot0(raw: bytes) -> Slot0:
    sqrt_price_x96 = int.from_bytes(raw[0:32], "big")
    tick = int.from_bytes(raw[32:64], "big")
    if tick >= 2**255:
        tick -= 2**256
    observation_index = int.from_bytes(raw[64:66], "big")
    observation_cardinality = int.from_bytes(raw[66:68], "big")
    fee_protocol = int.from_bytes(raw[68:70], "big")
    unlocked = raw[70] != 0

    return Slot0(
        sqrt_price_x96=sqrt_price_x96,
        tick=tick,
        observation_index=observation_index,
        observation_cardinality=observation_cardinality,
        fee_protocol=fee_protocol,
        unlocked=unlocked,
    )


async def fetch_v3_pool_state(
    rpc_caller,
    pool_address: str,
) -> V3PoolState | None:
    try:
        slot0_raw = await rpc_caller.call_contract(pool_address, SLOT0_SELECTOR)
        if not slot0_raw or slot0_raw == "0x":
            return None
        slot0_data = bytes.fromhex(slot0_raw[2:])
        slot0 = parse_slot0(slot0_data)

        liq_raw = await rpc_caller.call_contract(pool_address, LIQUIDITY_SELECTOR)
        if not liq_raw or liq_raw == "0x":
            return None
        liquidity = int.from_bytes(bytes.fromhex(liq_raw[2:]), "big")

        return V3PoolState(
            pool_address=pool_address,
            token0="",
            token1="",
            fee=3000,
            slot0=slot0,
            liquidity=liquidity,
            tick_spacing=60,
        )
    except Exception as e:
        logger.debug("Failed to fetch V3 pool state for %s: %s", pool_address[:10], e)
        return None
