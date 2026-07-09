from __future__ import annotations

import logging
from typing import Any

from agents.state import ArbitrageState, IngestionPayload, Status
from config.factories import MAJOR_ASSET_BLACKLIST
from config.settings import Settings

logger = logging.getLogger(__name__)


async def ingestion_node(state: ArbitrageState, settings: Settings) -> ArbitrageState:
    logger.info(
        "ingestion: run_id=%s token=%s liq_usd=%.2f",
        state.run_id,
        state.token_address,
        state.liq_usd,
    )

    if state.token_address.lower() in MAJOR_ASSET_BLACKLIST:
        state.status = Status.ABORTED
        state.reason = f"major asset blocked: {state.token_address}"
        logger.info("ingestion ABORT: %s reason=%s", state.run_id, state.reason)
        return state

    state.dry_run = settings.dry_run
    state.gas_usd = settings.gas_baseline_usd
    state.status = Status.PENDING
    return state


def build_ingestion_node(settings: Settings) -> Any:
    async def node(state: ArbitrageState) -> ArbitrageState:
        return await ingestion_node(state, settings)

    return node


def create_state_from_payload(payload: IngestionPayload, dry_run: bool = True) -> ArbitrageState:
    return ArbitrageState(
        run_id=payload.run_id,
        token_address=payload.token_address,
        pool_address=payload.pool_address,
        liq_usd=payload.liq_usd,
        is_verified=payload.is_verified,
        gross_spread_pct=payload.gross_spread_pct,
        trade_size_usd=payload.trade_size_usd,
        pool_reserve_usd=payload.pool_reserve_usd,
        buy_router=payload.buy_router,
        sell_router=payload.sell_router,
        dry_run=dry_run,
    )
