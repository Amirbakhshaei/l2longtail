from __future__ import annotations

import logging
from typing import Any

from agents.state import ArbitrageState, Status
from config.constants import ARBITRUM_CHAIN_ID, DEX_ROUTERS
from config.settings import Settings
from db.cache import ContractCache
from infra.keystore import Keystore
from infra.rpc_manager import RPCManager
from monitoring.alerts import TelegramAlerts

logger = logging.getLogger(__name__)


def _encode_swap_calldata(token_address: str, pool_address: str, trade_size_usd: float) -> str:
    selector = "0x38ed1739"
    token_padded = token_address.lower().replace("0x", "").zfill(64)
    pool_padded = pool_address.lower().replace("0x", "").zfill(64)
    amount_hex = format(int(trade_size_usd * 1e18), "064x")
    min_out = format(0, "064x")
    deadline = format(2**32 - 1, "064x")
    return f"{selector}{amount_hex}{min_out}{token_padded}{pool_padded}{deadline}"


async def execution_node(
    state: ArbitrageState,
    settings: Settings,
    cache: ContractCache,
    keystore: Keystore | None = None,
    rpc_manager: RPCManager | None = None,
    alerts: TelegramAlerts | None = None,
) -> ArbitrageState:
    if state.status != Status.VALIDATED:
        state.status = Status.ABORTED
        state.reason = "invalid upstream state"
        logger.info("execution ABORT: %s reason=%s", state.run_id, state.reason)
        return state

    if state.audit_is_safe is not True:
        state.status = Status.ABORTED
        state.reason = "audit not passed"
        logger.info("execution ABORT: %s reason=%s", state.run_id, state.reason)
        return state

    if state.net_profit_usd is None or state.net_profit_usd < settings.min_net_profit_usd:
        state.status = Status.ABORTED
        state.reason = "profit below execution floor"
        logger.info("execution ABORT: %s reason=%s", state.run_id, state.reason)
        return state

    state.status = Status.AUTHORIZED

    router_address = DEX_ROUTERS.get(state.sell_router, DEX_ROUTERS["uniswap_v2"])
    calldata = _encode_swap_calldata(
        state.token_address, state.pool_address, state.trade_size_usd
    )

    tx_payload: dict[str, object] = {
        "to": router_address,
        "data": calldata,
        "value": 0,
        "gas": 300000,
        "chainId": ARBITRUM_CHAIN_ID,
    }

    if state.dry_run:
        state.simulated_receipt = {
            "payload": tx_payload,
            "expected_gas_usd": state.gas_usd,
            "expected_net_profit_usd": state.net_profit_usd,
            "expected_slippage_pct": state.expected_slippage_pct,
            "token_address": state.token_address,
            "pool_address": state.pool_address,
            "trade_size_usd": state.trade_size_usd,
            "mode": "DRY_RUN",
        }
        state.status = Status.EXECUTED
        state.tx_hash = "0x" + "0" * 64

        logger.info(
            "[DRY RUN TEST SUITE OUTCOME] run_id=%s token=%s net_profit=$%.4f slippage=%.4f%%",
            state.run_id,
            state.token_address,
            state.net_profit_usd,
            state.expected_slippage_pct or 0,
        )
    else:
        if keystore is None or rpc_manager is None:
            state.status = Status.ABORTED
            state.reason = "live mode requires keystore and rpc_manager"
            return state

        gas_price = await rpc_manager.get_gas_price()
        nonce = await rpc_manager.get_transaction_count(keystore.address)
        tx_payload["gasPrice"] = gas_price
        tx_payload["nonce"] = nonce

        signed_tx = keystore.sign_transaction(tx_payload)
        raw_hex = signed_tx.raw_transaction.hex()

        tx_hash = await rpc_manager.send_private_transaction(raw_hex)

        if tx_hash:
            state.tx_hash = tx_hash
            state.status = Status.EXECUTED
            logger.info("execution LIVE: %s tx_hash=%s", state.run_id, tx_hash)
            if alerts:
                await alerts.send_telegram(
                    f"EXECUTED: {state.token_address}\n"
                    f"Profit: ${state.net_profit_usd:.4f}\n"
                    f"TX: {tx_hash}"
                )
        else:
            state.status = Status.ABORTED
            state.reason = "tx submission failed"
            logger.info("execution ABORT: %s reason=%s", state.run_id, state.reason)

    await cache.insert_execution_log(
        run_id=state.run_id,
        token_address=state.token_address,
        pool_address=state.pool_address,
        status=state.status.value,
        net_profit_usd=state.net_profit_usd,
        tx_hash=state.tx_hash,
        reason=state.reason,
        dry_run=state.dry_run,
    )

    return state


def build_execution_node(
    settings: Settings,
    cache: ContractCache,
    keystore: Keystore | None = None,
    rpc_manager: RPCManager | None = None,
    alerts: TelegramAlerts | None = None,
) -> Any:
    async def node(state: ArbitrageState) -> ArbitrageState:
        return await execution_node(state, settings, cache, keystore, rpc_manager, alerts)

    return node
