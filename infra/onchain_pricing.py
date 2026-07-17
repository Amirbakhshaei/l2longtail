"""
infra/onchain_pricing.py

On-chain quoting for Uniswap-V3-style routes via Uniswap V3 ``QuoterV2`` and
``Multicall3`` on Arbitrum.

We abandon local Python tick-state math (sqrtPriceX96 / liquidity bookkeeping)
and instead ask the EVM to price the route for us. The QuoterV2 contract
executes a ``quoteExactInput`` over the canonical V3 path off-chain (no state
change) and returns the exact ``amountOut`` for a given ``amountIn``.

To size the trade without a Ternary Search, we evaluate a fixed grid of input
sizes simultaneously in a single ``Multicall3.aggregate3`` call and pick the
grid point that maximises ``amountOut - amountIn`` (net WETH profit).

Path encoding
-------------
A V3 route is encoded as a tightly-packed byte string:

    TokenIn  (20 bytes)
    Fee      (3 bytes, uint24)
    TokenMid (20 bytes)
    Fee      (3 bytes, uint24)
    TokenOut (20 bytes)

i.e. ``['address', 'uint24', 'address', 'uint24', 'address']``. This is produced
with ``eth_abi.packed.encode_packed`` exactly as the V3 router expects.
"""
from __future__ import annotations

import logging
from typing import Any

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi import packed
from eth_utils import to_checksum_address

from config.constants import MULTICALL3_ADDRESS
from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

# Arbitrum mainnet addresses (per engineering directive).
MULTICALL3 = MULTICALL3_ADDRESS  # 0xcA11bde05977b3631167028862bE2a173976CA11
QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"

# Selectors.
QUOTE_EXACT_INPUT_SELECTOR = "cdca1758"  # quoteExactInput(bytes,uint256)
AGGREGATE3_SELECTOR = "82ad56cb"          # aggregate3((bool,address,bytes)[])

# Default grid of input sizes expressed in ETH, expanded to wei at runtime.
DEFAULT_GRID_ETH = [0.1, 0.5, 1.0, 2.0, 5.0]
ETH_WEI = 10 ** 18


def encode_v3_path(tokens: list[str], fees_bps: list[int]) -> bytes:
    """Pack an N-hop V3 route into the canonical ``bytes`` path.

    ``tokens`` is the ordered list of token addresses (length k+1).
    ``fees_bps`` is the per-hop fee tier in basis points (length k). Each fee
    is encoded as a 3-byte ``uint24``. The full layout is::

        ['address', 'uint24', 'address', ..., 'uint24', 'address']

    which is exactly the encoding the V3 router / QuoterV2 expects.
    """
    if len(tokens) != len(fees_bps) + 1:
        raise ValueError(
            f"tokens ({len(tokens)}) must be exactly one longer than "
            f"fees ({len(fees_bps)})"
        )
    types: list[str] = []
    values: list[Any] = []
    for i, token in enumerate(tokens):
        types.append("address")
        values.append(to_checksum_address(token))
        if i < len(fees_bps):
            types.append("uint24")
            # basis points -> Uniswap fee (1e-6 of notional)
            values.append(int(fees_bps[i] * 100))
    return packed.encode_packed(types, values)


def build_quote_call(path_bytes: bytes, amount_in_wei: int) -> str:
    """ABI-encode ``quoteExactInput(bytes path, uint256 amountIn)``."""
    calldata = abi_encode(
        ["bytes", "uint256"],
        [path_bytes, amount_in_wei],
    )
    return "0x" + QUOTE_EXACT_INPUT_SELECTOR + calldata.hex()


def build_aggregate3(quote_calls: list[tuple[str, str]]) -> str:
    """ABI-encode ``aggregate3((bool,address,bytes)[])``.

    ``quote_calls`` is a list of ``(target, callData_hex)`` pairs; all target
    the QuoterV2 contract. We set ``allowFailure = true`` so a single
    reverting quote does not abort the whole batch.
    """
    calls = [(True, QUOTER_V2, bytes.fromhex(c.replace("0x", ""))) for (_t, c) in quote_calls]
    calldata = abi_encode(
        ["(bool,address,bytes)[]"],
        [calls],
    )
    return "0x" + AGGREGATE3_SELECTOR + calldata.hex()


def decode_aggregate3(raw_hex: str) -> list[tuple[bool, bytes]]:
    """Decode ``aggregate3`` return ``(bool success, bytes returnData)[]``."""
    raw = raw_hex.replace("0x", "") if isinstance(raw_hex, str) else raw_hex
    if len(raw) < 64:
        return []
    data = bytes.fromhex(raw)
    try:
        (decoded,) = abi_decode(["(bool,bytes)[]"], data)
    except Exception:  # noqa: BLE001 - malformed payload, treat as no result
        return []
    return [(bool(success), ret) for (success, ret) in decoded]


def decode_quote_output(raw_bytes: bytes) -> int:
    """Extract ``amountOut`` (first uint256) from ``quoteExactInput`` return.

    quoteExactInput returns::

        (uint256 amountOut, uint160 sqrtPriceX96After,
         uint32 initializedTicksCrossed, uint256 gasEstimate)
    """
    if not raw_bytes or len(raw_bytes) < 32:
        return 0
    amount_out, *_ = abi_decode(
        ["uint256", "uint160", "uint32", "uint256"],
        raw_bytes,
    )
    return int(amount_out)


class OnChainQuoter:
    """Grid-search optimal input size for a V3 route via Multicall3."""

    def __init__(
        self,
        rpc_manager: RPCManager,
        quoter: str = QUOTER_V2,
        multicall3: str = MULTICALL3,
        grid_eth: list[float] | None = None,
    ) -> None:
        self.rpc = rpc_manager
        self.quoter = quoter
        self.multicall3 = multicall3
        self.grid = [
            int((g if g > 0 else 0) * ETH_WEI) for g in (grid_eth or DEFAULT_GRID_ETH)
        ]

    async def quote_best_size(
        self, tokens: list[str], fees_bps: list[int]
    ) -> tuple[int, int, int]:
        """Quote every grid point for the route and return the best one.

        Returns ``(best_amount_in_wei, best_amount_out_wei, best_net_wei)``
        where ``best_net_wei = best_amount_out - best_amount_in``. A return of
        ``(0, 0, 0)`` means no grid point produced a positive quote.
        """
        if len(tokens) < 2:
            return 0, 0, 0

        path_bytes = encode_v3_path(tokens, fees_bps)
        quote_calls = [
            (self.quoter, build_quote_call(path_bytes, size))
            for size in self.grid
        ]
        aggregate_calldata = build_aggregate3(quote_calls)

        result = await self.rpc.call_contract(self.multicall3, aggregate_calldata)
        if not result or result == "0x":
            logger.warning("On-chain quote: empty aggregate3 result")
            return 0, 0, 0

        decoded = decode_aggregate3(result)
        if not decoded:
            return 0, 0, 0

        best_in = 0
        best_out = 0
        best_net = 0
        for (success, ret), amount_in in zip(decoded, self.grid):
            if not success:
                continue
            amount_out = decode_quote_output(ret)
            net = amount_out - amount_in
            if net > best_net:
                best_net = net
                best_in = amount_in
                best_out = amount_out

        return best_in, best_out, best_net
