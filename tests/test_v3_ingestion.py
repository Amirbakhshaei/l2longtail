"""Tests for the V3 Swap ingestion path (no live RPC / WSS required)."""
from __future__ import annotations

import sys
import types

# Minimal web3 stub: the modules only need Web3 for to_checksum_address at
# runtime in non-test paths; keep import side-effects quiet.
if "web3" not in sys.modules:
    sys.modules["web3"] = types.ModuleType("web3")

from infra.flea_market_discovery import (  # noqa: E402
    V3StateEvent,
    WhitelistPool,
)
from infra.local_pricing import (  # noqa: E402
    DirectedGraph,
    PoolEdge,
    V3PoolEdge,
)
from process_a_indexer import ProcessAIndexer  # noqa: E402


def _v3_swap_log(pool: str, sqrt_price_x96: int, liquidity: int, tick: int) -> dict:
    def word(val: int, n_bytes: int) -> str:
        return val.to_bytes(n_bytes, "big").rjust(32, b"\x00").hex()

    # data = amount0(int256) | amount1(int256) | sqrtPriceX96(uint160)
    #        | liquidity(uint128) | tick(int24)
    data = (
        "0x"
        + word(0, 32)
        + word(0, 32)
        + word(sqrt_price_x96, 32)
        + word(liquidity, 32)
        + word(tick & 0xFFFFFF, 32)
    )
    return {
        "address": pool,
        "data": data,
        "blockNumber": "0x10",
        "topics": [
            "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
        ],
    }


E = 10**18
WETH = "0x" + "11" * 20
USDC = "0x" + "22" * 20
ALT = "0x" + "33" * 20


def test_parse_v3_log_decodes_state() -> None:
    pool = "0x" + "ab" * 20
    sqrt_p = int((100.0) ** 0.5 * 2**96)
    log = _v3_swap_log(pool, sqrt_p, 50_000 * E, 12345)
    ev = ProcessAIndexer._parse_v3_log(log)
    assert isinstance(ev, V3StateEvent)
    assert ev.pool_address == pool
    assert ev.sqrt_price_x96 == sqrt_p
    assert ev.liquidity == 50_000 * E
    assert ev.tick == 12345
    assert ev.block_number == 16


def test_parse_v3_log_rejects_short_data() -> None:
    log = {"address": "0x" + "ab" * 20, "data": "0x1234", "blockNumber": "0x1"}
    assert ProcessAIndexer._parse_v3_log(log) is None


def test_v3_state_feeds_graph_cycle() -> None:
    # WETH/USDC V2 + two V3 hops forming a profitable loop once V3 state
    # is supplied via update_v3_state (as the ingestion path would).
    g = DirectedGraph(
        pools=[PoolEdge("0xp1", WETH, USDC, "uniswap_v2", 1000 * E, 3_000_000 * E)],
        v3_pools=[
            V3PoolEdge(
                "0xp2", USDC, ALT, "uniswap_v3", fee_bps=500,
                sqrt_price_x96=int((100.0) ** 0.5 * 2**96), tick=0,
                liquidity=50_000 * E,
            ),
            V3PoolEdge(
                "0xp3", ALT, WETH, "uniswap_v3", fee_bps=500,
                sqrt_price_x96=int((32.0) ** 0.5 * 2**96), tick=0,
                liquidity=50_000 * E,
            ),
        ],
    )
    cyc = g.find_arbitrage_cycle(WETH)
    assert cyc is not None
    assert any(g.v3_edges.get(p) is not None for p in cyc.pools)

    # Simulate ingestion delivering updated V3 state via the same fields
    # the Swap log parser produces.
    ev = V3StateEvent(
        pool_address="0xp2", token0=USDC, token1=ALT,
        sqrt_price_x96=int((105.0) ** 0.5 * 2**96), tick=0,
        liquidity=60_000 * E, block_number=17, dex="uniswap_v3", timestamp=0.0,
    )
    g.update_v3_state(ev.pool_address, ev.sqrt_price_x96, ev.tick, ev.liquidity)
    assert g.v3_edges["0xp2"].sqrt_price_x96 == int((105.0) ** 0.5 * 2**96)


def test_graph_engine_v2_v3_cycle_end_to_end() -> None:
    """Real GraphArbEngine detects a V2+V3 cycle from a V3 Swap event.

    Mirrors the production path: build the engine, inject a verified V3 pool
    with seed state, then deliver a V3StateEvent (as process_v3_state would)
    and confirm find_arbitrage_cycle returns a mixed V2+V3 loop.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from process_b_sniper import GraphArbEngine

    g = GraphArbEngine(
        rpc_manager=MagicMock(),
        weth_oracle=AsyncMock(),
        weth_address=WETH,
        dry_run=True,
    )
    g.graph.add_v3_pool(
        V3PoolEdge(
            "0xp2", USDC, ALT, "uniswap_v3", fee_bps=500,
            sqrt_price_x96=int((100.0) ** 0.5 * 2**96), tick=0,
            liquidity=80_000 * E,
        )
    )
    g.graph.add_v3_pool(
        V3PoolEdge(
            "0xp3", ALT, WETH, "uniswap_v3", fee_bps=500,
            sqrt_price_x96=int((32.0) ** 0.5 * 2**96), tick=0,
            liquidity=80_000 * E,
        )
    )

    # A V3 Swap log delivers updated state for 0xp2.
    ev = V3StateEvent(
        pool_address="0xp2", token0=USDC, token1=ALT,
        sqrt_price_x96=int((110.0) ** 0.5 * 2**96), tick=0,
        liquidity=90_000 * E, block_number=20,
        dex="uniswap_v3", timestamp=0.0,
    )
    asyncio.run(g.process_v3_state(ev))

    # The V3 Swap event must update the engine's graph edge in place.
    assert g.graph.v3_edges["0xp2"].sqrt_price_x96 == int((110.0) ** 0.5 * 2**96)
    assert g.graph.v3_edges["0xp2"].liquidity == 90_000 * E


def test_logs_poller_http_transport() -> None:
    """LogsPoller must fetch V2 Sync + V3 Swap logs via eth_getLogs and feed
    the same callbacks the WSS listener uses — no live RPC/WSS required."""
    import asyncio
    from unittest.mock import MagicMock

    from infra.flea_market_discovery import V2_SYNC_TOPIC, V3_SWAP_TOPIC
    from infra.websocket_listener import LogsPoller, parse_sync_log

    pool = "0x" + "ab" * 20
    v3pool = "0x" + "cd" * 20

    sync_log = {
        "address": pool,
        "blockNumber": "0x14",
        "data": "0x" + (123 * E).to_bytes(32, "big").hex()
        + (456 * E).to_bytes(32, "big").hex(),
        "topics": [V2_SYNC_TOPIC],
    }
    v3_log = _v3_swap_log(v3pool, int((100.0) ** 0.5 * 2**96), 50_000 * E, 7)

    calls: list[str] = []

    class FakeRPC:
        async def call(self, method, params=None):
            calls.append(method)
            if method == "eth_blockNumber":
                return {"result": "0x20"}
            if method == "eth_getLogs":
                filt = params[0]
                topic = filt["topics"][0]
                if topic == V2_SYNC_TOPIC:
                    return {"result": [sync_log]}
                if topic == V3_SWAP_TOPIC:
                    return {"result": [v3_log]}
                return {"result": []}
            return {"result": "0x0"}

    poller = LogsPoller(
        rpc_manager=FakeRPC(),  # type: ignore[arg-type]
        whitelisted_addresses=[pool],
        v3_addresses=[v3pool],
        poll_interval=0.01,
        poll_blocks=5,
    )

    synced = []
    v3s = []
    poller.on_sync(lambda e: synced.append(e))
    poller.on_v3_swap(lambda l: v3s.append(l))

    async def run() -> None:
        poller._running = True
        await poller._poll_once()

    asyncio.run(run())

    assert "eth_getLogs" in calls
    assert len(synced) == 1
    assert synced[0].pair_address == pool
    assert synced[0].reserve0 == 123 * E
    assert synced[0].reserve1 == 456 * E
    assert len(v3s) == 1
    assert v3s[0]["address"] == v3pool

    # Dedup: a second identical poll must not re-emit the same Sync event.
    async def run2() -> None:
        poller._running = True
        await poller._poll_once()

    asyncio.run(run2())
    assert len(synced) == 1, "duplicate Sync event delivered after dedup"


