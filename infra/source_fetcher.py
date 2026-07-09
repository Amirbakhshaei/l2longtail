from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# Etherscan V2 API — unified endpoint for all chains
ETHERSCAN_V2_BASE_URL = "https://api.etherscan.io/v2/api"
ARBITRUM_CHAIN_ID = "42161"


class SourceFetcher:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("ETHERSCAN_API_KEY", "")

    async def fetch_source(self, token_address: str) -> str:
        return await fetch_contract_source(token_address, self.api_key)


async def fetch_contract_source(
    token_address: str,
    api_key: str,
) -> str:
    """Fetch verified contract source code from Etherscan V2 (Arbitrum)."""
    params = {
        "chainid": ARBITRUM_CHAIN_ID,
        "module": "contract",
        "action": "getsourcecode",
        "address": token_address,
        "apikey": api_key,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(ETHERSCAN_V2_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

    if data.get("status") != "1" or not data.get("result"):
        logger.warning(
            "Etherscan source fetch failed for %s: %s",
            token_address,
            data.get("message", "unknown error"),
        )
        return ""

    source_code = data["result"][0].get("SourceCode", "")
    if not source_code:
        logger.warning("No source code found for %s", token_address)
        return ""

    logger.info(
        "Fetched source for %s: %d chars",
        token_address,
        len(source_code),
    )
    return source_code
