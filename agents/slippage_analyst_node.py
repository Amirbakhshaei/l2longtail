from __future__ import annotations

import logging
from typing import Any

from agents.state import ArbitrageState, Status
from config.settings import Settings

logger = logging.getLogger(__name__)


async def slippage_analyst_node(
    state: ArbitrageState,
    settings: Settings,
) -> ArbitrageState:
    expected_slippage_pct = (state.trade_size_usd / state.pool_reserve_usd) * 100

    net_profit_usd = (
        ((state.gross_spread_pct - expected_slippage_pct) / 100)
        * state.trade_size_usd
        - state.gas_usd
    )

    state.expected_slippage_pct = round(expected_slippage_pct, 6)
    state.net_profit_usd = round(net_profit_usd, 4)

    if state.net_profit_usd < settings.min_net_profit_usd:
        state.status = Status.ABORTED
        state.reason = (
            f"net profit ${state.net_profit_usd:.4f} below "
            f"${settings.min_net_profit_usd:.2f} floor"
        )
        logger.info("slippage_analyst ABORT: %s reason=%s", state.run_id, state.reason)
        return state

    state.status = Status.VALIDATED
    logger.info(
        "slippage_analyst PASS: %s slippage=%.4f%% net_profit=$%.4f",
        state.run_id,
        state.expected_slippage_pct,
        state.net_profit_usd,
    )
    return state


def build_slippage_analyst_node(settings: Settings) -> Any:
    async def node(state: ArbitrageState) -> ArbitrageState:
        return await slippage_analyst_node(state, settings)

    return node
