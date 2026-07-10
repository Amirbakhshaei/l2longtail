from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

ETHERSCAN_V2_BASE_URL = "https://api.etherscan.io/v2/api"
ARBITRUM_CHAIN_ID = "42161"


class SourceFetcher:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("ETHERSCAN_API_KEY", "")
        if not self.api_key:
            logger.warning(
                "ETHERSCAN_API_KEY not set — contract source fetches will fail"
            )
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def fetch_source(self, token_address: str) -> str:
        if not self.api_key:
            return ""
        return await fetch_contract_source(token_address, self.api_key, self._client)


async def fetch_contract_source(
    token_address: str,
    api_key: str,
    client: httpx.AsyncClient | None = None,
) -> str:
    params = {
        "chainid": ARBITRUM_CHAIN_ID,
        "module": "contract",
        "action": "getsourcecode",
        "address": token_address,
        "apikey": api_key,
    }

    if client is not None:
        response = await client.get(ETHERSCAN_V2_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()
    else:
        async with httpx.AsyncClient(timeout=30.0) as fallback_client:
            response = await fallback_client.get(
                ETHERSCAN_V2_BASE_URL, params=params
            )
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
