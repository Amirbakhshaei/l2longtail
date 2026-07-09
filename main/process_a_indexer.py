"""
Process A: Background Indexer (Security & Discovery)

Heavy, slow, analytical. Runs in the background to build a whitelist.
Finds new tokens, audits their smart contracts, and builds a local database
of safe, tradable assets.

Flow:
A1: Factory Scanner → A2: Filter Gate → A3: LLM Auditor → A4: Database Manager
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from agents.minifier import minify_solidity
from config.factories import (
    MAJOR_ASSET_BLACKLIST,
    MAX_LIQUIDITY_USD,
    MIN_LIQUIDITY_USD,
)
from db.cleared_tokens import ClearedToken, ClearedTokensDB
from infra.flea_market_discovery import FleaMarketDiscovery
from infra.rpc_manager import RPCManager
from infra.source_fetcher import SourceFetcher

logger = logging.getLogger(__name__)


@dataclass
class AuditResult:
    is_safe: bool
    threats: list[str]


class LLMSecurityAuditor:
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.groq.com/openai/v1"

    async def audit(self, minified_source: str) -> AuditResult:
        if not minified_source or len(minified_source) < 100:
            return AuditResult(
                is_safe=False,
                threats=["insufficient source code"],
            )

        system_prompt = (
            "You are a Solidity security auditor. "
            "You will receive minified smart contract source code.\n\n"
            "Your task: inspect the code for these vulnerability classes:\n"
            "1. Hidden transfer taxes (fees on transfers not disclosed)\n"
            "2. Malicious mint mechanisms (unrestricted owner-only minting)\n"
            "3. Freeze or blacklist parameters (lock user funds or block)\n"
            "4. Balance modification vulnerabilities (direct manipulation)\n"
            "5. Honeypot patterns (buy allowed, sell blocked or penalized)\n\n"
            'Respond with JSON: {"is_safe": bool, "threats": [str]}\n'
            "- is_safe=true ONLY if NONE detected.\n"
            "- threats=empty list if none.\n"
            "RULES: Output ONLY JSON. No markdown, no prose.\n"
            "If code too short: is_safe=false, "
            'threats=["insufficient source code"].'
        )

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": minified_source[:8000]},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 500,
                    },
                )
                response.raise_for_status()
                data = response.json()

                content = data["choices"][0]["message"]["content"]
                content = content.strip()

                if content.startswith("```"):
                    content = content.split("\n", 1)[1]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()

                result = json.loads(content)
                return AuditResult(
                    is_safe=result.get("is_safe", False),
                    threats=result.get("threats", []),
                )

        except Exception as e:
            logger.error("LLM audit failed: %s", e)
            return AuditResult(
                is_safe=False,
                threats=[f"audit failed: {e}"],
            )


class ProcessAIndexer:
    def __init__(
        self,
        rpc_manager: RPCManager,
        source_fetcher: SourceFetcher,
        cleared_db: ClearedTokensDB,
        llm_auditor: LLMSecurityAuditor,
        flea_discovery: FleaMarketDiscovery,
    ) -> None:
        self.rpc = rpc_manager
        self.source_fetcher = source_fetcher
        self.db = cleared_db
        self.auditor = llm_auditor
        self.flea = flea_discovery
        self._running = False

    async def run(self, scan_interval: float = 60.0) -> None:
        self._running = True
        logger.info("Process A: Indexer started")

        while self._running:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error("Indexer scan cycle failed: %s", e)

            await asyncio.sleep(scan_interval)

    async def _scan_cycle(self) -> None:
        targets = await self.flea.scan_recent_pairs(lookback_blocks=1000)
        logger.info("Process A: Found %d new targets", len(targets))

        for target in targets:
            try:
                await self._process_target(target)
            except Exception as e:
                logger.error(
                    "Failed to process target %s: %s",
                    target.token_address[:10], e,
                )

    async def _process_target(self, target) -> None:
        from agents.state import FleaMarketTarget

        if not isinstance(target, FleaMarketTarget):
            return

        logger.info(
            "Processing %s on %s (liq=$%.0f)",
            target.token_address[:10],
            target.dex_venue_name,
            target.initial_liquidity_usd,
        )

        if not self._passes_filter_gate(target):
            return

        source = await self.source_fetcher.fetch_source(target.token_address)
        if not source:
            logger.warning("No source for %s", target.token_address[:10])
            return

        minified = minify_solidity(source)
        audit_result = await self.auditor.audit(minified)

        if not audit_result.is_safe:
            logger.warning(
                "Audit FAILED for %s: %s",
                target.token_address[:10],
                audit_result.threats,
            )
            return

        from infra.create2 import compute_v2_pair_address

        for dex_name in ["uniswap_v2", "sushiswap", "camelot_v2"]:
            pair = compute_v2_pair_address(
                dex_name,
                target.token_address,
                target.quote_address,
            )
            if pair:
                cleared_token = ClearedToken(
                    token_address=target.token_address,
                    symbol="",
                    name="",
                    dex_name=dex_name,
                    pair_address=pair.pair_address,
                    factory_address=pair.factory,
                    token0=pair.token0,
                    token1=pair.token1,
                    liquidity_usd=target.initial_liquidity_usd,
                    cleared_at=time.time(),
                    audit_is_safe=True,
                    audit_threats=[],
                )
                self.db.upsert_token(cleared_token)

        logger.info(
            "CLEARED: %s (audit passed, %d DEXes)",
            target.token_address[:10],
            3,
        )

    def _passes_filter_gate(self, target) -> bool:
        if target.token_address.lower() in MAJOR_ASSET_BLACKLIST:
            return False

        if target.initial_liquidity_usd < MIN_LIQUIDITY_USD:
            return False

        if target.initial_liquidity_usd > MAX_LIQUIDITY_USD:
            return False

        return True

    def stop(self) -> None:
        self._running = False
        logger.info("Process A: Indexer stopped")
