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

from infra.flea_market_discovery import SyncEvent, V2_SYNC_TOPIC

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
        max_backoff: float = MAX_BACKOFF,
    ) -> None:
        self.wss_url = wss_url
        self.whitelist = [a.lower() for a in whitelisted_addresses]
        self.whitelist_set = set(self.whitelist)
        self.sync_topic = sync_topic
        self.max_backoff = max_backoff

        self._running = False
        self._callbacks: list[Callable[[SyncEvent], Awaitable[None]]] = []
        self._ws: ClientConnection | None = None
        self._sub: SyncSubscription | None = None
        self._req_counter = 0
        self._seen: set[str] = set()
        self._events_processed = 0

    def on_sync(self, callback: Callable[[SyncEvent], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

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
        if params.get("subscription") != (self._sub.sub_id if self._sub else None):
            return

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

    def _parse_sync_log(self, log: dict) -> SyncEvent | None:
        try:
            pair_address = (log.get("address") or "").lower()
            if pair_address not in self.whitelist_set:
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
            if dedup_key in self._seen:
                return None
            self._seen.add(dedup_key)
            if len(self._seen) > 200_000:
                self._seen.clear()

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

    def stop(self) -> None:
        self._running = False
        logger.info(
            "WebSocket listener stopped (%d Sync events delivered)",
            self._events_processed,
        )
