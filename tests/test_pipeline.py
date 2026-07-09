import pytest

from agents.ingestion_node import create_state_from_payload
from agents.state import IngestionPayload
from config.settings import Settings
from db.blacklist import BlacklistDB
from db.cache import ContractCache
from graph.pipeline import compile_graph


@pytest.mark.asyncio
async def test_pipeline_happy_path_dry_run(
    settings: Settings,
    cache: ContractCache,
    blacklist_db: BlacklistDB,
) -> None:
    app = await compile_graph(settings, cache, blacklist_db)

    state = create_state_from_payload(
        IngestionPayload(
            run_id="pipe-001",
            token_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            liq_usd=50000.0,
            is_verified=True,
            gross_spread_pct=8.5,
            trade_size_usd=200.0,
            pool_reserve_usd=50000.0,
        ),
        dry_run=True,
    )

    config = {"configurable": {"thread_id": "pipe-001"}}
    result = await app.ainvoke(state.model_dump(), config=config)

    assert result["status"] in ("EXECUTED", "ABORTED")
    if result["status"] == "ABORTED":
        assert "audit" in result.get("reason", "")


@pytest.mark.asyncio
async def test_pipeline_aborts_unverified(
    settings: Settings,
    cache: ContractCache,
    blacklist_db: BlacklistDB,
) -> None:
    app = await compile_graph(settings, cache, blacklist_db)

    state = create_state_from_payload(
        IngestionPayload(
            run_id="pipe-002",
            token_address="0x1111111111111111111111111111111111111111",
            pool_address="0x2222222222222222222222222222222222222222",
            liq_usd=50000.0,
            is_verified=False,
            gross_spread_pct=10.0,
            trade_size_usd=100.0,
            pool_reserve_usd=50000.0,
        ),
        dry_run=True,
    )

    config = {"configurable": {"thread_id": "pipe-002"}}
    result = await app.ainvoke(state.model_dump(), config=config)

    assert result["status"] == "ABORTED"
    assert "unverified" in result.get("reason", "")


@pytest.mark.asyncio
async def test_pipeline_aborts_blacklisted(
    settings: Settings,
    cache: ContractCache,
    blacklist_db: BlacklistDB,
) -> None:
    await blacklist_db.add("0xdead000000000000000000000000000000000000", "test")
    app = await compile_graph(settings, cache, blacklist_db)

    state = create_state_from_payload(
        IngestionPayload(
            run_id="pipe-003",
            token_address="0xDEAD000000000000000000000000000000000000",
            pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            liq_usd=50000.0,
            is_verified=True,
            gross_spread_pct=12.0,
            trade_size_usd=200.0,
            pool_reserve_usd=50000.0,
        ),
        dry_run=True,
    )

    config = {"configurable": {"thread_id": "pipe-003"}}
    result = await app.ainvoke(state.model_dump(), config=config)

    assert result["status"] == "ABORTED"
    assert "blacklisted" in result.get("reason", "")


@pytest.mark.asyncio
async def test_pipeline_aborts_slippage_decay(
    settings: Settings,
    cache: ContractCache,
    blacklist_db: BlacklistDB,
) -> None:
    app = await compile_graph(settings, cache, blacklist_db)

    state = create_state_from_payload(
        IngestionPayload(
            run_id="pipe-004",
            token_address="0xDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD",
            pool_address="0xEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
            liq_usd=3000.0,
            is_verified=True,
            gross_spread_pct=3.0,
            trade_size_usd=500.0,
            pool_reserve_usd=3000.0,
        ),
        dry_run=True,
    )

    config = {"configurable": {"thread_id": "pipe-004"}}
    result = await app.ainvoke(state.model_dump(), config=config)

    assert result["status"] == "ABORTED"
