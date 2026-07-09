from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, Protocol

import httpx

from agents.minifier import minify_solidity
from agents.state import ArbitrageState, AuditResult, Status
from config.settings import Settings
from db.cache import ContractCache

logger = logging.getLogger(__name__)

MAX_SOURCE_CHARS = 8000


class SourceFetcher(Protocol):
    async def fetch_source(self, token_address: str) -> str: ...

AUDIT_SYSTEM_PROMPT = (
    "You are a Solidity security auditor. You will receive minified smart "
    "contract source code.\n\n"
    "Your task: inspect the code ONLY for these 5 vulnerability classes:\n"
    "1. Hidden transfer taxes (fees deducted on every transfer)\n"
    "2. Malicious mint mechanisms (unrestricted owner-only minting)\n"
    "3. Freeze or blacklist parameters (lock user funds)\n"
    "4. Balance modification vulnerabilities (direct balance manipulation)\n"
    "5. Honeypot patterns (buy allowed, sell blocked or penalized)\n\n"
    'Respond with a single valid JSON: {"is_safe": bool, "threats": [str]}\n'
    "- is_safe=true if NONE of the 5 classes above are DETECTED (not just suspected).\n"
    "- threats lists only confirmed vulnerabilities from the 5 classes. Empty list if none.\n"
    "- Do NOT list upgrade mechanisms, proxy patterns, or admin privileges as threats.\n"
    "- Do NOT add speculative threats like 'may hide' or 'could be used'.\n\n"
    "RULES:\n"
    "- Output ONLY the JSON object. No markdown, no prose.\n"
    "- If code is empty or unparseable, set is_safe=false and "
    'threats=["insufficient source code"].'
)


async def _call_llm(
    base_url: str,
    api_key: str,
    model: str,
    minified_source: str,
    temperature: float,
) -> AuditResult:
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    truncated = minified_source[:MAX_SOURCE_CHARS]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": AUDIT_SYSTEM_PROMPT},
            {"role": "user", "content": truncated},
        ],
        "temperature": temperature,
        "max_tokens": 256,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload, headers=headers)

        if response.status_code == 429:
            raise ValueError("rate limited by Groq (HTTP 429)")

        if response.status_code >= 400:
            body = response.text[:300]
            raise ValueError(f"HTTP {response.status_code}: {body}")

        data = response.json()

    if "error" in data:
        error_msg = data["error"].get("message", str(data["error"]))
        raise ValueError(f"API error: {error_msg}")

    if "choices" not in data or not data["choices"]:
        raise ValueError(f"No choices in response: {data}")

    content = data["choices"][0]["message"]["content"] or ""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

    return AuditResult.model_validate_json(content)


async def rag_auditor_node(
    state: ArbitrageState,
    settings: Settings,
    cache: ContractCache,
    rpc_manager: object | None = None,
    source_fetcher: SourceFetcher | None = None,
) -> ArbitrageState:
    cached = await cache.get_audit(state.token_address, ttl_hours=settings.cache_ttl_hours)
    if cached is not None:
        state.audit_is_safe = cached.is_safe
        state.audit_threats = cached.threats
        state.minified_source = cached.minified_source
        if not cached.is_safe:
            state.status = Status.ABORTED
            state.reason = f"audit failed (cached): {cached.threats}"
            logger.info("rag_auditor ABORT (cached): %s", state.run_id)
        else:
            state.status = Status.AUDITED
            logger.info("rag_auditor PASS (cached): %s", state.run_id)
        return state

    raw_source = ""
    if source_fetcher is not None:
        raw_source = await source_fetcher.fetch_source(state.token_address)
    elif rpc_manager is not None:
        rpc: RPCManagerProtocol = rpc_manager  # type: ignore[assignment]
        raw_source = await rpc.get_contract_source(state.token_address)

    state.minified_source = minify_solidity(raw_source)

    result: AuditResult | None = None
    for model in [settings.llm_model_primary, settings.llm_model_fallback]:
        for attempt in range(settings.llm_max_retries):
            try:
                result = await _call_llm(
                    base_url=settings.llm_base_url,
                    api_key=settings.llm_api_key,
                    model=model,
                    minified_source=state.minified_source,
                    temperature=settings.llm_temperature,
                )
                break
            except json.JSONDecodeError as e:
                logger.warning("rag_auditor JSON parse failure with model %s: %s", model, e)
                break
            except Exception as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "rate limit" in error_str.lower()
                if is_rate_limit and attempt < settings.llm_max_retries - 1:
                    delay = min(60, (3 * (2 ** attempt)) + random.uniform(0, 2))
                    logger.warning(
                        "rag_auditor rate limited, retrying in %.1fs (attempt %d/%d)",
                        delay, attempt + 1, settings.llm_max_retries,
                    )
                    await asyncio.sleep(delay)
                elif attempt < settings.llm_max_retries - 1:
                    delay = min(30, (2 * (2 ** attempt)) + random.uniform(0, 1))
                    logger.warning(
                        "rag_auditor error %s, retrying in %.1fs (attempt %d/%d)",
                        error_str[:100], delay, attempt + 1, settings.llm_max_retries,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning("rag_auditor failed with model %s: %s", model, error_str[:200])
                    break
        if result is not None:
            break

    if result is None:
        state.status = Status.ABORTED
        state.reason = "audit LLM failure on all models"
        logger.info("rag_auditor ABORT: %s reason=%s", state.run_id, state.reason)
        return state

    state.audit_is_safe = result.is_safe
    state.audit_threats = result.threats

    await cache.store_source(
        state.token_address, state.is_verified, raw_source, state.minified_source
    )
    await cache.store_audit(
        state.token_address, result.is_safe, result.threats, state.minified_source
    )

    if not result.is_safe:
        state.status = Status.ABORTED
        state.reason = f"audit failed: {result.threats}"
        logger.info("rag_auditor ABORT: %s threats=%s", state.run_id, result.threats)
    else:
        state.status = Status.AUDITED
        logger.info("rag_auditor PASS: %s", state.run_id)

    return state


class RPCManagerProtocol:
    async def get_contract_source(self, token_address: str) -> str:
        return ""


def build_rag_auditor_node(
    settings: Settings,
    cache: ContractCache,
    rpc_manager: Any = None,
    source_fetcher: SourceFetcher | None = None,
) -> Any:
    async def node(state: ArbitrageState) -> ArbitrageState:
        return await rag_auditor_node(state, settings, cache, rpc_manager, source_fetcher)

    return node
