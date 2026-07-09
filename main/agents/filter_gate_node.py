from __future__ import annotations

import logging
from typing import Any

from agents.state import ArbitrageState, Status
from config.settings import Settings
from db.blacklist import BlacklistDB

logger = logging.getLogger(__name__)


async def filter_gate_node(
    state: ArbitrageState,
    settings: Settings,
    blacklist_db: BlacklistDB,
) -> ArbitrageState:
    if not state.is_verified:
        state.status = Status.ABORTED
        state.reason = "unverified contract bytecode"
        logger.info("filter_gate ABORT: %s reason=%s", state.run_id, state.reason)
        return state

    if state.liq_usd < settings.min_liquidity_usd:
        state.status = Status.ABORTED
        min_liq = settings.min_liquidity_usd
        state.reason = f"liquidity ${state.liq_usd:.2f} below ${min_liq:.0f} floor"
        logger.info("filter_gate ABORT: %s reason=%s", state.run_id, state.reason)
        return state

    is_blacklisted = await blacklist_db.contains(state.token_address)
    if is_blacklisted:
        state.status = Status.ABORTED
        state.reason = "blacklisted token address"
        logger.info("filter_gate ABORT: %s reason=%s", state.run_id, state.reason)
        return state

    if state.trade_size_usd > settings.max_trade_size_usd:
        state.status = Status.ABORTED
        state.reason = (
            f"trade size ${state.trade_size_usd:.2f} exceeds "
            f"${settings.max_trade_size_usd:.0f} cap"
        )
        logger.info("filter_gate ABORT: %s reason=%s", state.run_id, state.reason)
        return state

    state.status = Status.FILTERED
    logger.info("filter_gate PASS: %s", state.run_id)
    return state


def build_filter_gate_node(settings: Settings, blacklist_db: BlacklistDB) -> Any:
    async def node(state: ArbitrageState) -> ArbitrageState:
        return await filter_gate_node(state, settings, blacklist_db)

    return node
