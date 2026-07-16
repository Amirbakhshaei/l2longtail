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

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from infra.flea_market_discovery import SyncEvent, V2_SYNC_TOPIC, V3_SWAP_TOPIC

logger = logging.getLogger(__name__)

SUBSCRIBE_METHOD = "eth_subscribe"
UNSUBSCRIBE_METHOD = "eth_unsubscribe"
BASE_BACKOFF = 1.0
MAX_BACKOFF = 30.0
RECONNECT_JITTER = 0.25


@dataclass
class SyncSubscription:
    """Handle returned by the sequencer for an active subscription."""

    sub_id: str
    req_id: int


class WebSocketListener:
    """
    Low-latency WSS consumer for Sync events on a whitelist of pools.

    Usage::

        listener = WebSocketListener(wss_url, whitelisted_addresses)
        listener.on_sync(handle_sync)
        await listener.listen()
    """

    def __init__(
        self,
        wss_url: str,
        whitelisted_addresses: list[str],
        sync_topic: str = V2_SYNC_TOPIC,
        v3_addresses: list[str] | None = None,
        max_backoff: float = MAX_BACKOFF,
    ) -> None:
        self.wss_url = wss_url
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
        self._req_counter = 0
        self._seen: set[str] = set()
        self._events_processed = 0

    def on_sync(self, callback: Callable[[SyncEvent], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

    def on_v3_swap(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self._v3_callbacks.append(callback)

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    async def listen(self) -> None:
        """Block forever, maintaining the WSS subscription with backoff."""
        self._running = True
        attempt = 0
        while self._running:
            try:
                await self._connect_and_stream()
                attempt = 0
            except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                if not self._running:
                    break
                attempt += 1
                backoff = min(BASE_BACKOFF * (2 ** (attempt - 1)), self.max_backoff)
                logger.warning(
                    "WSS dropped (%s) — reconnecting in %.1fs (attempt %d)",
                    e,
                    backoff,
                    attempt,
                )
                await asyncio.sleep(backoff)

    async def _connect_and_stream(self) -> None:
        async with websockets.connect(self.wss_url, ping_interval=15, ping_timeout=10) as ws:
            self._ws = ws
            logger.info("WSS connected: %s", self.wss_url)

            sub = await self._subscribe()
            if sub is None:
                logger.error("Subscription handshake failed; terminating stream")
                return

            self._sub = sub
            logger.info(
                "Subscribed to Sync logs on %d whitelisted pools (sub=%s)",
                len(self.whitelist),
                sub.sub_id[:18],
            )

            if self.v3_addresses and self._v3_callbacks:
                v3_sub = await self._subscribe_v3()
                if v3_sub is None:
                    logger.warning(
                        "V3 subscription handshake failed; V3 pools ignored"
                    )
                else:
                    self._v3_sub = v3_sub
                    logger.info(
                        "Subscribed to V3 Swap logs on %d pools (sub=%s)",
                        len(self.v3_addresses),
                        v3_sub.sub_id[:18],
                    )

            async for raw in ws:
                if not self._running:
                    break
                await self._dispatch(raw)

    async def _subscribe(self) -> SyncSubscription | None:
        assert self._ws is not None
        self._req_counter += 1
        req_id = self._req_counter
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": SUBSCRIBE_METHOD,
            "params": [
                {
                    "subscription": "logs",
                    "logs": {
                        "address": self.whitelist,
                        "topics": [self.sync_topic],
                    },
                }
            ],
        }
        await self._ws.send(json.dumps(payload))
        resp = await self._recv_json()
        if resp is None:
            return None
        result = resp.get("result")
        if not result:
            logger.error("eth_subscribe returned no id: %s", resp)
            return None
        return SyncSubscription(sub_id=result, req_id=req_id)

    async def _subscribe_v3(self) -> SyncSubscription | None:
        assert self._ws is not None
        self._req_counter += 1
        req_id = self._req_counter
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": SUBSCRIBE_METHOD,
            "params": [
                {
                    "subscription": "logs",
                    "logs": {
                        "address": self.v3_addresses,
                        "topics": [V3_SWAP_TOPIC],
                    },
                }
            ],
        }
        await self._ws.send(json.dumps(payload))
        resp = await self._recv_json()
        if resp is None:
            return None
        result = resp.get("result")
        if not result:
            logger.error("eth_subscribe (V3) returned no id: %s", resp)
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
        if sub_id == (self._sub.sub_id if self._sub else None):
            log = params.get("result", {})
            event = self._parse_sync_log(log)
            if event is None:
                return

            self._events_processed += 1
            for callback in self._callbacks:
                try:
                    await callback(event)
                except Exception as e:  # noqa: BLE001 - never kill the stream
                    logger.error("Sync callback failed: %s", e)
        elif sub_id == (self._v3_sub.sub_id if self._v3_sub else None):
            log = params.get("result", {})
            if parse_v3_log(log, self.v3_set) is None:
                return

            self._events_processed += 1
            for callback in self._v3_callbacks:
                try:
                    await callback(log)
                except Exception as e:  # noqa: BLE001 - never kill the stream
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
        poll_interval: float = 4.0,
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
        """Poll forever with bounded backoff on RPC errors."""
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
