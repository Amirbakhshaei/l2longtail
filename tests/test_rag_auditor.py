import pytest

from agents.rag_auditor_node import AUDIT_SYSTEM_PROMPT, rag_auditor_node
from agents.state import ArbitrageState, Status
from config.settings import Settings
from db.cache import ContractCache


@pytest.mark.asyncio
async def test_rag_auditor_uses_cache_hit(
    sample_state: ArbitrageState,
    settings: Settings,
    cache: ContractCache,
) -> None:
    await cache.store_source(
        sample_state.token_address, True, "raw code", "minified code"
    )
    await cache.store_audit(sample_state.token_address, True, [], "minified code")

    result = await rag_auditor_node(sample_state, settings, cache, rpc_manager=None)
    assert result.status == Status.AUDITED
    assert result.audit_is_safe is True
    assert result.audit_threats == []


@pytest.mark.asyncio
async def test_rag_auditor_cache_hit_unsafe(
    sample_state: ArbitrageState,
    settings: Settings,
    cache: ContractCache,
) -> None:
    await cache.store_source(
        sample_state.token_address, True, "raw code", "minified code"
    )
    await cache.store_audit(
        sample_state.token_address, False, ["honeypot detected"], "minified code"
    )

    result = await rag_auditor_node(sample_state, settings, cache, rpc_manager=None)
    assert result.status == Status.ABORTED
    assert "cached" in result.reason


def test_audit_system_prompt_exists() -> None:
    assert "Solidity security auditor" in AUDIT_SYSTEM_PROMPT
    assert "is_safe" in AUDIT_SYSTEM_PROMPT
    assert "threats" in AUDIT_SYSTEM_PROMPT
