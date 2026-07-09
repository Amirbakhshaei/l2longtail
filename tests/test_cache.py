
import pytest

from db.cache import ContractCache


@pytest.mark.asyncio
async def test_cache_store_and_get_source(cache: ContractCache) -> None:
    await cache.store_source("0xabc", True, "raw source", "minified source")
    result = await cache.get_source("0xabc")
    assert result is not None
    assert result.is_verified is True
    assert result.raw_source == "raw source"
    assert result.minified_source == "minified source"


@pytest.mark.asyncio
async def test_cache_miss(cache: ContractCache) -> None:
    result = await cache.get_source("0xnonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_cache_ttl_expiry(cache: ContractCache) -> None:
    await cache.store_source("0xabc", True, "raw", "minified")
    result = await cache.get_source("0xabc", ttl_hours=0)
    assert result is None


@pytest.mark.asyncio
async def test_cache_store_and_get_audit(cache: ContractCache) -> None:
    await cache.store_source("0xdef", True, "raw", "minified")
    await cache.store_audit("0xdef", True, [], "minified")
    result = await cache.get_audit("0xdef")
    assert result is not None
    assert result.is_safe is True
    assert result.threats == []


@pytest.mark.asyncio
async def test_cache_audit_with_threats(cache: ContractCache) -> None:
    await cache.store_source("0xghi", True, "raw", "minified")
    await cache.store_audit("0xghi", False, ["honeypot", "hidden tax"], "minified")
    result = await cache.get_audit("0xghi")
    assert result is not None
    assert result.is_safe is False
    assert len(result.threats) == 2


@pytest.mark.asyncio
async def test_cache_purge_expired(cache: ContractCache) -> None:
    await cache.store_source("0xold", True, "raw", "minified")
    deleted = await cache.purge_expired(ttl_hours=0)
    assert deleted >= 0


@pytest.mark.asyncio
async def test_execution_log_insert(cache: ContractCache) -> None:
    await cache.insert_execution_log(
        run_id="test-run",
        token_address="0xabc",
        pool_address="0xdef",
        status="EXECUTED",
        net_profit_usd=1.50,
        tx_hash="0x123",
        reason=None,
        dry_run=True,
    )
