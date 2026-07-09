import pytest

from agents.slippage_analyst_node import slippage_analyst_node
from agents.state import ArbitrageState, Status
from config.settings import Settings


@pytest.mark.asyncio
async def test_slippage_passes_profitable_trade(
    sample_state: ArbitrageState,
    settings: Settings,
) -> None:
    result = await slippage_analyst_node(sample_state, settings)
    assert result.status == Status.VALIDATED
    assert result.expected_slippage_pct is not None
    assert result.net_profit_usd is not None
    assert result.net_profit_usd >= 0.50


@pytest.mark.asyncio
async def test_slippage_aborts_unprofitable_trade(
    settings: Settings,
) -> None:
    state = ArbitrageState(
        run_id="slip-001",
        token_address="0xDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD",
        pool_address="0xEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
        liq_usd=3000.0,
        is_verified=True,
        gross_spread_pct=3.0,
        trade_size_usd=500.0,
        pool_reserve_usd=3000.0,
    )
    result = await slippage_analyst_node(state, settings)
    assert result.status == Status.ABORTED
    assert result.net_profit_usd is not None
    assert result.net_profit_usd < 0.50
    assert "net profit" in result.reason


@pytest.mark.asyncio
async def test_slippage_calculation_accuracy(
    settings: Settings,
) -> None:
    state = ArbitrageState(
        run_id="slip-002",
        token_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        liq_usd=50000.0,
        is_verified=True,
        gross_spread_pct=10.0,
        trade_size_usd=1000.0,
        pool_reserve_usd=50000.0,
    )
    result = await slippage_analyst_node(state, settings)

    expected_slippage = (1000.0 / 50000.0) * 100
    expected_profit = ((10.0 - expected_slippage) / 100) * 1000.0 - 0.02

    assert abs(result.expected_slippage_pct - expected_slippage) < 0.001
    assert abs(result.net_profit_usd - expected_profit) < 0.01


@pytest.mark.asyncio
async def test_slippage_boundary_at_floor(
    settings: Settings,
) -> None:
    state = ArbitrageState(
        run_id="slip-003",
        token_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        liq_usd=100000.0,
        is_verified=True,
        gross_spread_pct=0.006,
        trade_size_usd=10000.0,
        pool_reserve_usd=100000.0,
    )
    result = await slippage_analyst_node(state, settings)
    assert result.net_profit_usd is not None
    if result.net_profit_usd < 0.50:
        assert result.status == Status.ABORTED
    else:
        assert result.status == Status.VALIDATED
