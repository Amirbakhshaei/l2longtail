from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from infra.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)

MAX_429_RETRIES = 3
BASE_BACKOFF = 2.0


class RPCManager:
    def __init__(
        self,
        primary_url: str,
        fallback_url: str,
        flashbots_url: str,
        rate_limiter: TokenBucketRateLimiter,
    ) -> None:
        self.primary_url = primary_url
        self.fallback_url = fallback_url
        self.flashbots_url = flashbots_url
        self.rate_limiter = rate_limiter
        self._failover_active = False
        self._consecutive_failures = 0
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _http_post(
        self, url: str, method: str, params: list[Any]
    ) -> dict[str, Any]:
        client = await self._get_client()
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")
        return data

    async def call(
        self, method: str, params: list[Any] | None = None
    ) -> dict[str, Any]:
        if params is None:
            params = []

        await self.rate_limiter.acquire()
        url = self.fallback_url if self._failover_active else self.primary_url

        try:
            response = await self._http_post(url, method, params)
            self._consecutive_failures = 0
            return response
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                for attempt in range(MAX_429_RETRIES):
                    backoff = BASE_BACKOFF * (2**attempt)
                    retry_after = e.response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            backoff = max(backoff, float(retry_after))
                        except ValueError:
                            pass
                    logger.warning(
                        "Rate limited (429), retrying in %.1fs (attempt %d/%d)",
                        backoff,
                        attempt + 1,
                        MAX_429_RETRIES,
                    )
                    await asyncio.sleep(backoff)
                    await self.rate_limiter.acquire()
                    try:
                        return await self._http_post(url, method, params)
                    except httpx.HTTPStatusError as retry_e:
                        if retry_e.response.status_code == 429:
                            continue
                        raise
                logger.error("Rate limit persists after %d retries", MAX_429_RETRIES)
                raise
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._failover_active = not self._failover_active
                self._consecutive_failures = 0
                logger.warning(
                    "RPC failover toggled, active=%s", self._failover_active
                )
            retry_url = (
                self.fallback_url if not self._failover_active else self.primary_url
            )
            await self.rate_limiter.acquire()
            return await self._http_post(retry_url, method, params)
        except (httpx.ConnectError, httpx.TimeoutException):
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._failover_active = not self._failover_active
                self._consecutive_failures = 0
                logger.warning(
                    "RPC failover toggled, active=%s", self._failover_active
                )
            retry_url = (
                self.fallback_url if not self._failover_active else self.primary_url
            )
            await self.rate_limiter.acquire()
            return await self._http_post(retry_url, method, params)

    async def get_block_number(self) -> int:
        result = await self.call("eth_blockNumber")
        return int(result["result"], 16)

    async def get_gas_price(self) -> int:
        result = await self.call("eth_gasPrice")
        return int(result["result"], 16)

    async def get_transaction_count(self, address: str) -> int:
        result = await self.call("eth_getTransactionCount", [address, "latest"])
        return int(result["result"], 16)

    async def get_contract_source(self, token_address: str) -> str:
        result = await self.call("eth_getCode", [token_address, "latest"])
        val: str = result.get("result", "0x")
        return val

    async def send_private_transaction(self, signed_raw_hex: str) -> str | None:
        try:
            result = await self._http_post(
                self.flashbots_url,
                "eth_sendRawTransaction",
                [signed_raw_hex],
            )
            return result.get("result")
        except Exception as e:
            logger.error("Flashbots submission failed: %s", e)
            return None

    async def call_contract(self, to: str, data: str) -> str:
        result = await self.call("eth_call", [{"to": to, "data": data}, "latest"])
        val: str = result.get("result", "0x")
        return val
