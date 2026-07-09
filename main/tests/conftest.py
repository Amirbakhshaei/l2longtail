import os
import tempfile
from collections.abc import AsyncGenerator

import pytest

from agents.state import ArbitrageState, IngestionPayload
from config.settings import Settings
from db.blacklist import BlacklistDB
from db.cache import ContractCache


@pytest.fixture
def settings() -> Settings:
    return Settings(
        dry_run=True,
        alchemy_api_key="test",
        llm_api_key="test",
        llm_base_url="https://fake.api.com/v1",
        keystore_passphrase="test",
        db_path=":memory:",
    )


@pytest.fixture
def tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
async def cache(tmp_db_path: str) -> AsyncGenerator[ContractCache, None]:
    c = ContractCache(tmp_db_path)
    await c.init()
    yield c


@pytest.fixture
async def blacklist_db(tmp_db_path: str) -> AsyncGenerator[BlacklistDB, None]:
    db = BlacklistDB(tmp_db_path)
    cache = ContractCache(tmp_db_path)
    await cache.init()
    yield db


@pytest.fixture
def sample_payload() -> IngestionPayload:
    return IngestionPayload(
        run_id="test-001",
        token_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        liq_usd=50000.0,
        is_verified=True,
        gross_spread_pct=8.5,
        trade_size_usd=200.0,
        pool_reserve_usd=50000.0,
    )


@pytest.fixture
def sample_state(sample_payload: IngestionPayload) -> ArbitrageState:
    return ArbitrageState(
        run_id=sample_payload.run_id,
        token_address=sample_payload.token_address,
        pool_address=sample_payload.pool_address,
        liq_usd=sample_payload.liq_usd,
        is_verified=sample_payload.is_verified,
        gross_spread_pct=sample_payload.gross_spread_pct,
        trade_size_usd=sample_payload.trade_size_usd,
        pool_reserve_usd=sample_payload.pool_reserve_usd,
    )
