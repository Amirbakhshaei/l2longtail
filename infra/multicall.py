from __future__ import annotations

from typing import Any

from config.constants import MULTICALL3_ADDRESS

AGGREGATE_SELECTOR = "252dba42"


def encode_multicall3_aggregate(calls: list[dict[str, Any]]) -> str:
    encoded_calls: list[tuple[str, str]] = []
    for call in calls:
        target = call["target"].lower().replace("0x", "").zfill(64)
        call_data = call["callData"].replace("0x", "")
        encoded_calls.append((target, call_data))

    parts: list[str] = []
    parts.append(AGGREGATE_SELECTOR)
    parts.append("0000000000000000000000000000000000000000000000000000000000000020")
    parts.append(format(len(calls), "064x"))

    dynamic_offset = 32 * len(calls)
    offsets: list[str] = []
    for i in range(len(calls)):
        offsets.append(format(dynamic_offset, "064x"))
        dynamic_offset += 64 + (len(encoded_calls[i][1]) // 2) + 32

    parts.extend(offsets)

    for target, call_data in encoded_calls:
        parts.append(target)
        data_len = len(call_data) // 2
        parts.append(format(data_len, "064x"))
        parts.append(call_data.ljust(64, "0"))

    return "0x" + "".join(parts)


def decode_multicall3_results(raw_hex: str) -> list[bytes]:
    data = raw_hex.replace("0x", "")
    if len(data) < 128:
        return []

    _count = int(data[64:128], 16)
    results: list[bytes] = []
    pos = 128

    offsets: list[int] = []
    for _ in range(_count):
        offset = int(data[pos : pos + 64], 16)
        offsets.append(offset)
        pos += 64

    for offset in offsets:
        abs_pos = 128 + offset * 2
        length = int(data[abs_pos : abs_pos + 64], 16)
        result_data = data[abs_pos + 64 : abs_pos + 64 + length * 2]
        results.append(bytes.fromhex(result_data))

    return results


class MulticallBatcher:
    def __init__(self, rpc_manager: Any) -> None:
        self.rpc_manager = rpc_manager
        self.multicall3_address = MULTICALL3_ADDRESS

    async def aggregate(self, calls: list[dict[str, Any]]) -> list[bytes]:
        encoded = encode_multicall3_aggregate(calls)
        result = await self.rpc_manager.call_contract(self.multicall3_address, encoded)
        return decode_multicall3_results(result)
