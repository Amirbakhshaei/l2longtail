from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import orjson

from infra.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)

# Native Rust hot-path (src/lib.rs -> alloy_executor, built by Maturin).
# Imported lazily so the pure-Python fallback works when the extension is
# absent. See rust_bridge.md for the cross-compilation boundary.
try:
    import alloy_executor as _alloy_executor  # type: ignore
    _HAS_ALLOY = True
except Exception:  # noqa: BLE001
    _alloy_executor = None
    _HAS_ALLOY = False

MAX_429_RETRIES = 3
BASE_BACKOFF = 2.0


class RPCManager:
    def __init__(
        self,
        primary_url: str,
        fallback_url: str,
        flashbots_url: str,
        rate_limiter: TokenBucketRateLimiter,
        execution_rpcs: list[str] | None = None,
    ) -> None:
        self.primary_url = primary_url
        self.fallback_url = fallback_url
        self.flashbots_url = flashbots_url
        self.rate_limiter = rate_limiter
        # Concurrent broadcast endpoints. Arbitrum is FCFS (no PGA), so we
        # win on latency: the signed payload is shotgunned to every endpoint
        # at once and the first one to reach the sequencer wins. Sourced from
        # EXECUTION_RPCS (comma-separated); falls back to the primary +
        # fallback so single-endpoint deployments still work unchanged.
        self.execution_rpcs: list[str] = list(execution_rpcs or [])
        if not self.execution_rpcs:
            self.execution_rpcs = [u for u in (primary_url, fallback_url) if u]
        self._failover_active = False
        self._consecutive_failures = 0
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=5.0,
            limits=httpx.Limits(
                max_keepalive_connections=100,
                max_connections=100,
            ),
        )

    async def close(self) -> None:
        await self._client.aclose()

    def reset_connection(self) -> None:
        """Force-close the underlying HTTP/2 connection pool so the next
        ``call`` opens a fresh socket. Cheap no-op if already closed; used by
        the LogsPoller to silently recover from idle-provider connection resets
        without logging a warning."""
        try:
            self._client = httpx.AsyncClient(
                http2=True,
                timeout=5.0,
                limits=httpx.Limits(
                    max_keepalive_connections=100,
                    max_connections=100,
                ),
            )
            self._failover_active = False
            self._consecutive_failures = 0
        except Exception:  # noqa: BLE001
            pass

    async def _http_post(
        self, url: str, method: str, params: list[Any]
    ) -> dict[str, Any]:
        # orjson serializes the JSON-RPC envelope faster than the stdlib
        # json encoder, reducing CPU overhead during high-frequency polling.
        payload = orjson.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        )
        response = await self._client.post(
            url,
            content=payload,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code >= 400:
            logger.error(
                "HTTP %d on %s: %s", response.status_code, method, response.text
            )

        response.raise_for_status()
        data: dict[str, Any] = orjson.loads(response.content)
        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")
        return data

    @staticmethod
    def _is_auth_error(exc: BaseException) -> bool:
        """A provider auth failure (missing/revoked API key, 401/403). These
        are permanent for a given key and must fail over immediately rather
        than burning the transient-retry budget."""
        if isinstance(exc, httpx.HTTPStatusError):
            if exc.response.status_code in (401, 403):
                return True
            body = (exc.response.text or "").lower()
            if "unauthorized" in body or "forbidden" in body or "api key" in body:
                return True
        elif isinstance(exc, RuntimeError):
            msg = str(exc).lower()
            if "unauthorized" in msg or "forbidden" in msg or "api key" in msg:
                return True
        return False

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
            if self._is_auth_error(e):
                # Permanent auth failure — switch to the fallback RPC and stay
                # there. Retrying the same keyed endpoint is futile.
                if not self._failover_active:
                    self._failover_active = True
                    logger.warning(
                        "RPC auth failure on primary — failing over to fallback RPC"
                    )
                await self.rate_limiter.acquire()
                return await self._http_post(
                    self.fallback_url, method, params
                )
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
                self.fallback_url if self._failover_active else self.primary_url
            )
            await self.rate_limiter.acquire()
            return await self._http_post(retry_url, method, params)
        except RuntimeError as e:
            if self._is_auth_error(e):
                if not self._failover_active:
                    self._failover_active = True
                    logger.warning(
                        "RPC auth failure on primary — failing over to fallback RPC"
                    )
                await self.rate_limiter.acquire()
                return await self._http_post(self.fallback_url, method, params)
            raise
        except (httpx.ConnectError, httpx.TimeoutException):
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._failover_active = not self._failover_active
                self._consecutive_failures = 0
                logger.warning(
                    "RPC failover toggled, active=%s", self._failover_active
                )
            retry_url = (
                self.fallback_url if self._failover_active else self.primary_url
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

    async def eth_call(self, to: str, data: str) -> str:
        """Execute a stateless ``eth_call`` (no state change) and return the
        raw hex result. Thin wrapper over ``call`` for callers that already
        hold the target address + calldata."""
        result = await self.call("eth_call", [{"to": to, "data": data}, "latest"])
        return result.get("result", "0x")

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

    @staticmethod
    def _is_collision_error(exc: BaseException) -> bool:
        """A subsequent shotgun RPC (the tx already landed via a faster peer)
        returns a collision. These are expected and silent: nonce too low,
        already known, already imported, replacement tx underpriced, etc."""
        msg = str(exc).lower()
        collision_markers = (
            "nonce too low",
            "already known",
            "already imported",
            "transaction already",
            "replacement transaction underpriced",
            "nonce is too low",
            "known transaction",
        )
        if any(m in msg for m in collision_markers):
            return True
        # JSON-RPC error objects embed the message under data/message.
        if isinstance(exc, RuntimeError) and "error" in msg:
            return any(m in msg for m in collision_markers)
        return False

    async def broadcast_transaction(self, signed_tx_hex: str) -> list[str | None]:
        """Simultaneous broadcast (shotgun) of a single signed raw tx to every
        execution RPC concurrently. Arbitrum's FCFS sequencer means the first
        endpoint to deliver wins on latency; the rest return expected collision
        errors which are silently dropped. Returns the list of tx hashes (or
        None per endpoint) in RPC order."""

        async def _send(url: str) -> str | None:
            try:
                result = await self._http_post(
                    url, "eth_sendRawTransaction", [signed_tx_hex]
                )
                return result.get("result")
            except Exception as e:  # noqa: BLE001
                if self._is_collision_error(e):
                    return None
                # Auth/transport failures on a single peer are non-fatal:
                # another endpoint in the fan-out likely succeeded. Stay quiet
                # to avoid log spam during the hot path.
                logger.debug("broadcast to %s failed: %s", url, e)
                return None

        results = await asyncio.gather(
            *(_send(url) for url in self.execution_rpcs),
            return_exceptions=True,
        )
        # return_exceptions=True keeps a Task-group shape; surface any stray
        # BaseException (e.g. CancelledError) as None rather than crashing.
        cleaned: list[str | None] = [
            r if not isinstance(r, BaseException) else None for r in results
        ]
        submitted = [h for h in cleaned if h]
        if submitted:
            logger.info(
                "SHOTGUN broadcast: %d/%d endpoints accepted (tx=%s)",
                len(submitted),
                len(self.execution_rpcs),
                submitted[0],
            )
        else:
            logger.warning(
                "SHOTGUN broadcast: no endpoint accepted tx across %d endpoints",
                len(self.execution_rpcs),
            )
        return cleaned

    # Phase-1 baseline name required by the hybrid architecture spec. Thin
    # alias over broadcast_transaction so both entry points stay in sync.
    async def broadcast_raw_tx(self, signed_tx_hex: str) -> list:
        """Multi-RPC simultaneous broadcast (shotgun). Fires the signed raw
        tx to every endpoint in ``execution_rpcs`` concurrently via
        ``asyncio.gather(return_exceptions=True)``. Late-arriving peers return
        expected duplicate/collision errors ("already known", "nonce too
        low") which are silently ignored. Returns the per-endpoint result
        list (tx hash or None) in RPC order."""
        return await self.broadcast_transaction(signed_tx_hex)

    async def broadcast_ex(
        self,
        path_tokens: list[str],
        fees: list[int],
        amount_in_wei: int,
        private_key: str,
        to_address: str,
        chain_id: int = 42161,
    ) -> list:
        """High-level hybrid broadcast. Prefers the native Rust hot-path
        (``alloy_executor.ex_broadcast`` — GIL-free, concurrent Tokio blast)
        and raises a clear error when the extension is unavailable, since the
        pure-Python path (``broadcast_raw_tx``) requires a pre-signed hex
        rather than raw signing inputs. See rust_bridge.md for the boundary."""
        if not _HAS_ALLOY:
            raise RuntimeError(
                "alloy_executor native extension not built — run `maturin "
                "develop`. Use RPCManager.broadcast_raw_tx with a pre-signed "
                "hex for the pure-Python fallback."
            )
        return _alloy_executor.ex_broadcast(
            path_tokens,
            [int(f) for f in fees],
            str(amount_in_wei),
            private_key,
            list(self.execution_rpcs),
            to_address,
            chain_id,
        )
