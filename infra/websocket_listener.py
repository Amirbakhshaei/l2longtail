"""
Real-time WebSocket sequencer feed for Uniswap-V2-style Sync events.

Subscribes to the `logs` stream over WSS, filtering by the V2 `Sync` event
topic (0x1c411e9a...) and the loaded whitelist pool addresses. This removes
HTTP polling latency from discovery: a Sync fires on every trade, and the
payload is parsed and pushed downstream the instant the sequencer emits it.

Reconnection uses exponential backoff with a hard ceiling. The connection is a
raw JSON-RPC 2.0 over `eth_subscribe` — no heavyweight web3 provider required.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from infra.flea_market_discovery import V2_SYNC_TOPIC, V3_SWAP_TOPIC, SyncEvent

logger = logging.getLogger(__name__)

# Exceptions that indicate an idle HTTP/2 connection was reset / closed by the
# RPC provider. These are routine during polling and must NOT be logged as
# warnings — they are handled by reconnecting silently and continuing the loop.
try:  # pragma: no cover - h2 is always present via httpx[http2]
    from h2.exceptions import (
        ConnectionError as H2ConnectionError,
        ProtocolError as H2ProtocolError,
    )
except Exception:  # noqa: BLE001 - h2 optional; fall back to valid base classes
    H2ConnectionError = ConnectionError
    H2ProtocolError = ConnectionError

# Guard: only keep entries that are actual exception classes. A fallback that
# resolved to a non-class (e.g. an empty tuple) must never enter the tuple,
# or `except <tuple>` raises "catching classes that do not inherit from
# BaseException is not allowed".
def _is_exc_class(obj: object) -> bool:
    return isinstance(obj, type) and issubclass(obj, BaseException)


_DISCONNECT_EXCEPTIONS = tuple(
    e
    for e in (
        H2ConnectionError,
        H2ProtocolError,
        ConnectionResetError,
        BrokenPipeError,
        httpx.RemoteProtocolError,
        httpx.ProtocolError,
        httpx.TransportError,
        httpx.ConnectError,
        httpx.HTTPError,
    )
    if _is_exc_class(e)
)


SUBSCRIBE_METHOD = "eth_subscribe"
UNSUBSCRIBE_METHOD = "eth_unsubscribe"
BASE_BACKOFF = 1.0
MAX_BACKOFF = 30.0
RECONNECT_JITTER = 0.25

# Watchdog: Arbitrum block time is ~0.25s. If we receive ZERO frames
# (newHeads OR logs) within this window, the provider has silently dropped
# the socket and we must rotate to the next URL in the pool instantly.
SILENT_DROP_TIMEOUT = 2.0


@dataclass
class SyncSubscription:
    """Handle returned by the sequencer for an active subscription."""

    sub_id: str
    req_id: int


class WebSocketListener:
    """
    Low-latency WSS consumer with a WSS Watchdog and Instant Rotation.

    Accepts a pool of free-tier WSS URLs and rotates through them
    sequentially. A ``newHeads`` subscription acts as the liveness
    heartbeat; the ``logs`` subscription carries V2 ``Sync`` + V3 ``Swap``
    events. Every socket read is wrapped in ``asyncio.wait_for(..., 2.0)`` —
    if the provider goes silent (free-tier idle-drop) the watchdog force-closes
    the socket, advances the URL index, and reconnects to the next provider.

    Usage::

        listener = WebSocketListener([wss_url_a, wss_url_b], whitelisted_addresses)
        listener.on_sync(handle_sync)
        await listener.listen()
    """

    def __init__(
        self,
        wss_urls: str | list[str],
        whitelisted_addresses: list[str],
        sync_topic: str = V2_SYNC_TOPIC,
        v3_addresses: list[str] | None = None,
        max_backoff: float = MAX_BACKOFF,
    ) -> None:
        if isinstance(wss_urls, str):
            wss_urls = [wss_urls]
        self.wss_pool = [u for u in wss_urls if u]
        if not self.wss_pool:
            raise ValueError("WebSocketListener requires at least one WSS URL")
        self._url_index = 0

        self.whitelist = [a.lower() for a in whitelisted_addresses]
        self.whitelist_set = set(self.whitelist)
        self.sync_topic = sync_topic
        self.v3_addresses = [a.lower() for a in (v3_addresses or [])]
        self.v3_set = set(self.v3_addresses)
        self.max_backoff = max_backoff

        self._running = False
        self._callbacks: list[Callable[[SyncEvent], Awaitable[None]]] = []
        self._v3_callbacks: list[Callable[[dict], Awaitable[None]]] = []
        self._ws: ClientConnection | None = None
        self._sub: SyncSubscription | None = None
        self._v3_sub: SyncSubscription | None = None
        self._head_sub: SyncSubscription | None = None
        self._req_counter = 0
        self._seen: set[str] = set()
        self._events_processed = 0
        self._last_head_ts: float = 0.0

    @property
    def wss_url(self) -> str:
        """The URL the watchdog will connect to next (round-robin)."""
        return self.wss_pool[self._url_index % len(self.wss_pool)]

    def _rotate_url(self) -> str:
        """Advance to the next provider in the pool and return it."""
        self._url_index = (self._url_index + 1) % len(self.wss_pool)
        return self.wss_url

    def on_sync(self, callback: Callable[[SyncEvent], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

    def on_v3_swap(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self._v3_callbacks.append(callback)

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    async def listen(self) -> None:
        """Block forever, maintaining WSS with watchdog + instant rotation."""
        self._running = True
        attempt = 0
        while self._running:
            try:
                await self._connect_and_stream()
                attempt = 0
            except (TimeoutError, ConnectionClosed, OSError) as e:
                if not self._running:
                    break
                attempt += 1
                backoff = min(BASE_BACKOFF * (2 ** (attempt - 1)), self.max_backoff)
                next_url = self._rotate_url()
                logger.warning(
                    "WSS dropped (%s) on %s — rotating to %s, reconnect in %.1fs "
                    "(attempt %d)",
                    e,
                    self.wss_url,
                    next_url,
                    backoff,
                    attempt,
                )
                await asyncio.sleep(backoff)

    async def _connect_and_stream(self) -> None:
        url = self.wss_url
        # Strict protocol-level heartbeat: the library sends a ping every 5s and
        # expects a pong within 5s; a missed pong raises ConnectionClosed, which
        # listen() catches to instantly rotate to the next provider in the pool.
        async with websockets.connect(url, ping_interval=5.0, ping_timeout=5.0) as ws:
            self._ws = ws
            self._head_sub = None
            self._sub = None
            self._v3_sub = None
            logger.info("WSS connected: %s", url)

            # newHeads acts as the liveness heartbeat for the watchdog.
            head_sub = await self._subscribe_new_heads()
            if head_sub is None:
                # Raise so listen() catches it and rotates to the next URL
                # in the pool (e.g. provider rejects eth_subscribe).
                raise OSError("newHeads subscription rejected")
            self._head_sub = head_sub

            # Single logs subscription carrying both V2 Sync and V3 Swap topics
            # across the full whitelist — one eth_subscribe covers everything.
            sub = await self._subscribe_logs()
            if sub is None:
                raise OSError("logs subscription rejected")
            self._sub = sub
            if self.v3_addresses and self._v3_callbacks:
                self._v3_sub = sub  # same sub_id delivers both topics
            logger.info(
                "Subscribed: heads=%s logs(V2=%d V3=%d) on %s",
                head_sub.sub_id[:18],
                len(self.whitelist),
                len(self.v3_addresses),
                url,
            )

            self._last_head_ts = time.monotonic()
            # Silent-Drop Defense: wrap every read in a 2s watchdog. Arbitrum
            # emits a block ~every 0.25s, so 2s of silence means we were
            # dropped. Raise TimeoutError -> caught by listen() -> rotate URL.
            while self._running:
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=SILENT_DROP_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "WSS silent for %.1fs on %s — provider dropped, rotating",
                        SILENT_DROP_TIMEOUT,
                        url,
                    )
                    raise
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                await self._dispatch(raw)

    async def _subscribe_new_heads(self) -> SyncSubscription | None:
        assert self._ws is not None
        self._req_counter += 1
        req_id = self._req_counter
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": SUBSCRIBE_METHOD,
            "params": [{"subscription": "newHeads"}],
        }
        await self._ws.send(json.dumps(payload))
        resp = await self._recv_json()
        if resp is None:
            return None
        if "error" in resp:
            logger.error(
                "eth_subscribe(newHeads) rejected by %s: %s",
                self.wss_url,
                resp["error"],
            )
            return None
        result = resp.get("result")
        if not result:
            logger.error("eth_subscribe(newHeads) returned no id: %s", resp)
            return None
        return SyncSubscription(sub_id=result, req_id=req_id)

    async def _subscribe_logs(self) -> SyncSubscription | None:
        assert self._ws is not None
        self._req_counter += 1
        req_id = self._req_counter
        topics: list[str] = [self.sync_topic]
        if self.v3_addresses and self._v3_callbacks:
            topics.append(V3_SWAP_TOPIC)
        # Union of all addresses we care about; the topics array filters by
        # either V2 Sync OR V3 Swap, so a single subscription suffices.
        addresses = list(self.whitelist)
        if self.v3_addresses and self._v3_callbacks:
            addresses = addresses + self.v3_addresses
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": SUBSCRIBE_METHOD,
            "params": [
                {
                    "subscription": "logs",
                    "logs": {
                        "address": addresses,
                        "topics": topics,
                    },
                }
            ],
        }
        await self._ws.send(json.dumps(payload))
        resp = await self._recv_json()
        if resp is None:
            return None
        if "error" in resp:
            logger.error(
                "eth_subscribe(logs) rejected by %s: %s",
                self.wss_url,
                resp["error"],
            )
            return None
        result = resp.get("result")
        if not result:
            logger.error("eth_subscribe(logs) returned no id: %s", resp)
            return None
        return SyncSubscription(sub_id=result, req_id=req_id)

    async def _recv_json(self) -> dict[str, Any] | None:
        assert self._ws is not None
        raw = await self._ws.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Non-JSON frame ignored: %r", raw[:80])
            return None

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #
    async def _dispatch(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        method = msg.get("method")
        if method != "eth_subscription":
            return

        params = msg.get("params", {})
        sub_id = params.get("subscription")

        # newHeads heartbeat — refreshes the watchdog liveness timestamp.
        if sub_id == (self._head_sub.sub_id if self._head_sub else None):
            self._last_head_ts = time.monotonic()
            return

        # logs subscription: delivers both V2 Sync (whitelist) and V3 Swap
        # (v3_set) under a single sub_id.
        if sub_id == (self._sub.sub_id if self._sub else None):
            log = params.get("result", {})
            address = (log.get("address") or "").lower()

            # V2 Sync path.
            if address in self.whitelist_set:
                event = self._parse_sync_log(log)
                if event is None:
                    return
                self._events_processed += 1
                for callback in self._callbacks:
                    try:
                        await callback(event)
                    except Exception as e:  # noqa: BLE001 - never kill stream
                        logger.error("Sync callback failed: %s", e)
                return

            # V3 Swap path (same subscription).
            if self.v3_addresses and self._v3_callbacks and address in self.v3_set:
                if parse_v3_log(log, self.v3_set) is None:
                    return
                self._events_processed += 1
                for callback in self._v3_callbacks:
                    try:
                        await callback(log)
                    except Exception as e:  # noqa: BLE001 - never kill stream
                        logger.error("V3 Swap callback failed: %s", e)

    def _parse_sync_log(self, log: dict) -> SyncEvent | None:
        return parse_sync_log(log, self.whitelist_set, self._seen)


def parse_sync_log(
    log: dict, whitelist_set: set[str], seen: set[str]
) -> SyncEvent | None:
    """Parse a V2 `Sync` log entry into a SyncEvent. Shared by the WSS
    listener and the HTTP LogsPoller so both feeds produce identical events."""
    try:
        pair_address = (log.get("address") or "").lower()
        if pair_address not in whitelist_set:
            return None

        data_hex = (log.get("data") or "0x").replace("0x", "")
        if len(data_hex) < 128:
            return None

        reserve0 = int(data_hex[0:64], 16)
        reserve1 = int(data_hex[64:128], 16)
        if reserve0 == 0 and reserve1 == 0:
            return None

        block_number = int(log.get("blockNumber", "0x0"), 16)

        dedup_key = f"{pair_address}:{block_number}:{reserve0}:{reserve1}"
        if dedup_key in seen:
            return None
        seen.add(dedup_key)
        if len(seen) > 200_000:
            seen.clear()

        return SyncEvent(
            pair_address=pair_address,
            token0="",
            token1="",
            reserve0=reserve0,
            reserve1=reserve1,
            block_number=block_number,
            dex="",
            timestamp=time.time(),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to parse Sync log: %s", e)
        return None


def parse_v3_log(log: dict, v3_set: set[str]) -> dict | None:
    """Validate a V3 `Swap` log belongs to a whitelisted V3 pool. Returns the
    raw log (already address-filtered) or None. Shared by both transports."""
    try:
        address = (log.get("address") or "").lower()
        if address not in v3_set:
            return None
        return log
    except Exception:  # noqa: BLE001
        return None


class LogsPoller:
    """HTTP ``eth_getLogs`` polling transport.

    Used when no WebSocket endpoint is available (e.g. a free Ankr HTTPS RPC
    that exposes ``eth_getLogs`` but not ``eth_subscribe``). It periodically
    fetches logs for the V2 ``Sync`` and V3 ``Swap`` topics across the
    whitelisted pool addresses and feeds the same callbacks as the WSS
    listener, so downstream logic is transport-agnostic.

    Latency is bounded by ``poll_interval`` (seconds); cost is one batched
    ``eth_getLogs`` call per topic per poll (addresses are passed as an array).
    """

    def __init__(
        self,
        rpc_manager: "RPCManager",
        whitelisted_addresses: list[str],
        sync_topic: str = V2_SYNC_TOPIC,
        v3_addresses: list[str] | None = None,
        poll_interval: float = 1.5,
        poll_blocks: int = 5,
        max_backoff: float = 30.0,
    ) -> None:
        from infra.rpc_manager import RPCManager

        self.rpc: RPCManager = rpc_manager
        self.whitelist = [a.lower() for a in whitelisted_addresses]
        self.whitelist_set = set(self.whitelist)
        self.sync_topic = sync_topic
        self.v3_addresses = [a.lower() for a in (v3_addresses or [])]
        self.v3_set = set(self.v3_addresses)
        self.poll_interval = poll_interval
        self.poll_blocks = poll_blocks
        self.max_backoff = max_backoff

        self._running = False
        self._callbacks: list[Callable[[SyncEvent], Awaitable[None]]] = []
        self._v3_callbacks: list[Callable[[dict], Awaitable[None]]] = []
        self._seen: set[str] = set()
        self._last_block: int | None = None
        self._events_processed = 0

    def on_sync(self, callback: Callable[[SyncEvent], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

    def on_v3_swap(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self._v3_callbacks.append(callback)

    async def _get_logs(
        self, topic: str, addresses: list[str], from_block: int, to_block: int
    ) -> list[dict]:
        if not addresses:
            return []
        filter_obj = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "topics": [topic],
            "address": addresses,
        }
        data = await self.rpc.call("eth_getLogs", [filter_obj])
        return data.get("result", []) or []

    async def _poll_once(self) -> None:
        head = await self.rpc.call("eth_blockNumber")
        head_block = int(head.get("result", "0x0"), 16)
        if self._last_block is None:
            from_block = max(0, head_block - self.poll_blocks)
        else:
            from_block = self._last_block + 1
        to_block = head_block
        if from_block > to_block:
            return
        self._last_block = to_block

        # V2 Sync logs.
        v2_logs = await self._get_logs(
            self.sync_topic, self.whitelist, from_block, to_block
        )
        for log in v2_logs:
            event = parse_sync_log(log, self.whitelist_set, self._seen)
            if event is None:
                continue
            self._events_processed += 1
            for cb in self._callbacks:
                try:
                    await cb(event)
                except Exception as e:  # noqa: BLE001
                    logger.error("Sync callback failed: %s", e)

        # V3 Swap logs.
        if self.v3_addresses and self._v3_callbacks:
            v3_logs = await self._get_logs(
                V3_SWAP_TOPIC, self.v3_addresses, from_block, to_block
            )
            for log in v3_logs:
                if parse_v3_log(log, self.v3_set) is None:
                    continue
                self._events_processed += 1
                for cb in self._v3_callbacks:
                    try:
                        await cb(log)
                    except Exception as e:  # noqa: BLE001
                        logger.error("V3 Swap callback failed: %s", e)

    async def listen(self) -> None:
        """Poll forever with bounded backoff on RPC errors.

        Standard RPC server disconnects (HTTP/2 connection resets on an idle
        socket, dropped keep-alive, etc.) are expected when polling a third
        party endpoint and are *not* logged as warnings — they are swallowed
        silently, the connection is reset, and the loop continues.
        """
        self._running = True
        attempt = 0
        logger.info(
            "LogsPoller started: interval=%.1fs blocks=%d pools=%d v3=%d",
            self.poll_interval,
            self.poll_blocks,
            len(self.whitelist),
            len(self.v3_addresses),
        )
        while self._running:
            try:
                await self._poll_once()
                attempt = 0
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except _DISCONNECT_EXCEPTIONS:
                # Idle HTTP/2 connection reset / protocol state errors from the
                # RPC provider. Reconnect silently and continue the loop — do
                # not surface as [WARNING] or [ERROR].
                if not self._running:
                    break
                attempt = 0
                self.rpc.reset_connection()
                await asyncio.sleep(self.poll_interval)
            except Exception as e:  # noqa: BLE001
                if not self._running:
                    break
                attempt += 1
                backoff = min(self.poll_interval * (2 ** (attempt - 1)), self.max_backoff)
                logger.warning(
                    "LogsPoller error (%s) — retrying in %.1fs (attempt %d)",
                    e,
                    backoff,
                    attempt,
                )
                await asyncio.sleep(backoff)

    def stop(self) -> None:
        self._running = False
        logger.info(
            "LogsPoller stopped (%d Sync events delivered)", self._events_processed
        )
