import pytest

from agents.execution_node import execution_node
from agents.state import ArbitrageState, Status
from config.settings import Settings
from db.cache import ContractCache


@pytest.mark.asyncio
async def test_execution_dry_run_success(
    settings: Settings,
    cache: ContractCache,
) -> None:
    state = ArbitrageState(
        run_id="exec-001",
        token_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        liq_usd=50000.0,
        is_verified=True,
        gross_spread_pct=8.5,
        trade_size_usd=200.0,
        pool_reserve_usd=50000.0,
        status=Status.VALIDATED,
        audit_is_safe=True,
        audit_threats=[],
        expected_slippage_pct=0.4,
        net_profit_usd=16.18,
        dry_run=True,
    )

    result = await execution_node(state, settings, cache)
    assert result.status == Status.EXECUTED
    assert result.tx_hash == "0x" + "0" * 64
    assert result.simulated_receipt is not None
    assert result.simulated_receipt["mode"] == "DRY_RUN"
    assert result.simulated_receipt["expected_net_profit_usd"] == 16.18


@pytest.mark.asyncio
async def test_execution_aborts_invalid_upstream(
    settings: Settings,
    cache: ContractCache,
) -> None:
    state = ArbitrageState(
        run_id="exec-002",
        token_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        liq_usd=50000.0,
        is_verified=True,
        gross_spread_pct=8.5,
        trade_size_usd=200.0,
        pool_reserve_usd=50000.0,
        status=Status.PENDING,
        audit_is_safe=True,
        net_profit_usd=16.18,
    )

    result = await execution_node(state, settings, cache)
    assert result.status == Status.ABORTED
    assert "invalid upstream" in result.reason


@pytest.mark.asyncio
async def test_execution_aborts_audit_not_passed(
    settings: Settings,
    cache: ContractCache,
) -> None:
    state = ArbitrageState(
        run_id="exec-003",
        token_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        liq_usd=50000.0,
        is_verified=True,
        gross_spread_pct=8.5,
        trade_size_usd=200.0,
        pool_reserve_usd=50000.0,
        status=Status.VALIDATED,
        audit_is_safe=False,
        net_profit_usd=16.18,
    )

    result = await execution_node(state, settings, cache)
    assert result.status == Status.ABORTED
    assert "audit" in result.reason


@pytest.mark.asyncio
async def test_execution_aborts_below_profit_floor(
    settings: Settings,
    cache: ContractCache,
) -> None:
    state = ArbitrageState(
        run_id="exec-004",
        token_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        liq_usd=50000.0,
        is_verified=True,
        gross_spread_pct=8.5,
        trade_size_usd=200.0,
        pool_reserve_usd=50000.0,
        status=Status.VALIDATED,
        audit_is_safe=True,
        net_profit_usd=0.10,
    )

    result = await execution_node(state, settings, cache)
    assert result.status == Status.ABORTED
    assert "profit" in result.reason
