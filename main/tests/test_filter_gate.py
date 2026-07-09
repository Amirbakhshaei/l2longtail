import pytest

from agents.filter_gate_node import filter_gate_node
from agents.state import ArbitrageState, Status
from config.settings import Settings
from db.blacklist import BlacklistDB


@pytest.mark.asyncio
async def test_filter_gate_passes_valid_token(
    sample_state: ArbitrageState,
    settings: Settings,
    blacklist_db: BlacklistDB,
) -> None:
    result = await filter_gate_node(sample_state, settings, blacklist_db)
    assert result.status == Status.FILTERED
    assert result.reason is None


@pytest.mark.asyncio
async def test_filter_gate_aborts_unverified(
    sample_state: ArbitrageState,
    settings: Settings,
    blacklist_db: BlacklistDB,
) -> None:
    sample_state.is_verified = False
    result = await filter_gate_node(sample_state, settings, blacklist_db)
    assert result.status == Status.ABORTED
    assert "unverified" in result.reason


@pytest.mark.asyncio
async def test_filter_gate_aborts_low_liquidity(
    sample_state: ArbitrageState,
    settings: Settings,
    blacklist_db: BlacklistDB,
) -> None:
    sample_state.liq_usd = 100.0
    result = await filter_gate_node(sample_state, settings, blacklist_db)
    assert result.status == Status.ABORTED
    assert "liquidity" in result.reason


@pytest.mark.asyncio
async def test_filter_gate_aborts_blacklisted(
    sample_state: ArbitrageState,
    settings: Settings,
    blacklist_db: BlacklistDB,
) -> None:
    await blacklist_db.add(sample_state.token_address, "test blacklist")
    result = await filter_gate_node(sample_state, settings, blacklist_db)
    assert result.status == Status.ABORTED
    assert "blacklisted" in result.reason


@pytest.mark.asyncio
async def test_filter_gate_aborts_oversized_trade(
    sample_state: ArbitrageState,
    settings: Settings,
    blacklist_db: BlacklistDB,
) -> None:
    sample_state.trade_size_usd = 999999.0
    result = await filter_gate_node(sample_state, settings, blacklist_db)
    assert result.status == Status.ABORTED
    assert "trade size" in result.reason
