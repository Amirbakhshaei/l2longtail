"""
CREATE2 deterministic pair address calculator for Uniswap V2 forks.

Eliminates getPair() RPC calls by computing pair addresses locally.
Formula: pair = keccak256(0xff + factory + keccak256(sort(tokenA, tokenB)) + init_code_hash)[12:]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from web3 import Web3

logger = logging.getLogger(__name__)

INIT_CODE_HASHES: dict[str, str] = {
    "uniswap_v2": "0x96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f",
    "sushiswap": "0xe18a34eb0e04b04f7a0ac29a6e80748dca96319b42c54d679cb821dca90c6303",
    "camelot_v2": "0xa856464ae65f761957085a0ac62c4362c6ad614b5ad849393375979e90141a90",
}

FACTORY_ADDRESSES: dict[str, str] = {
    "uniswap_v2": "0xf1d7cc64fb4452f05c498126312ebe29f30fbcf9",
    "sushiswap": "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
    "camelot_v2": "0x6EcCab422D763aC031210895C81787E87B43A652",
    "trader_joe": "0xaE3e9C2103BDEd932f13C206aC7be48780F31312",
}


@dataclass
class PairAddress:
    dex_name: str
    factory: str
    token0: str
    token1: str
    pair_address: str


def _normalize_address(addr: str) -> str:
    return addr.lower().replace("0x", "").zfill(40)


def _to_checksum(addr: str) -> str:
    return Web3.to_checksum_address("0x" + _normalize_address(addr))


def _sort_tokens(token_a: str, token_b: str) -> tuple[str, str]:
    a = _normalize_address(token_a)
    b = _normalize_address(token_b)
    if a < b:
        return "0x" + a, "0x" + b
    return "0x" + b, "0x" + a


def compute_v2_pair_address(
    dex_name: str,
    token_a: str,
    token_b: str,
) -> PairAddress | None:
    factory = FACTORY_ADDRESSES.get(dex_name)
    if not factory:
        logger.warning("No factory address for DEX: %s", dex_name)
        return None

    init_code_hash = INIT_CODE_HASHES.get(dex_name)
    if not init_code_hash or init_code_hash == "0x" + "0" * 64:
        logger.debug("No init code hash for DEX: %s, need RPC fallback", dex_name)
        return None

    token0, token1 = _sort_tokens(token_a, token_b)

    salt = Web3.solidity_keccak(
        ["address", "address"],
        [_to_checksum(token0), _to_checksum(token1)],
    )

    factory_bytes = bytes.fromhex(_normalize_address(factory))
    salt_bytes = bytes.fromhex(salt.hex() if isinstance(salt, bytes) else salt.replace("0x", ""))
    init_code_bytes = bytes.fromhex(init_code_hash.replace("0x", ""))

    raw_hash = Web3.keccak(
        b"\xff" + factory_bytes + salt_bytes + init_code_bytes
    )

    pair_address = "0x" + raw_hash.hex()[-40:]

    return PairAddress(
        dex_name=dex_name,
        factory=factory,
        token0=token0,
        token1=token1,
        pair_address=pair_address,
    )


async def find_pair_address(
    rpc_caller,
    dex_name: str,
    token_a: str,
    token_b: str,
) -> PairAddress | None:
    computed = compute_v2_pair_address(dex_name, token_a, token_b)
    if computed:
        return computed

    return await _fallback_get_pair(rpc_caller, dex_name, token_a, token_b)


def compute_all_v2_pairs(
    token_a: str,
    token_b: str,
) -> list[PairAddress]:
    pairs = []
    for dex_name in FACTORY_ADDRESSES:
        pair = compute_v2_pair_address(dex_name, token_a, token_b)
        if pair:
            pairs.append(pair)
    return pairs


async def verify_pair_address(
    rpc_caller,
    dex_name: str,
    token_a: str,
    token_b: str,
) -> PairAddress | None:
    return await find_pair_address(rpc_caller, dex_name, token_a, token_b)


async def _fallback_get_pair(
    rpc_caller,
    dex_name: str,
    token_a: str,
    token_b: str,
) -> PairAddress | None:
    factory = FACTORY_ADDRESSES.get(dex_name)
    if not factory:
        return None

    try:
        from infra.pair_finder import _encode_get_pair
        call_data = _encode_get_pair(token_a, token_b)
        raw = await rpc_caller.call_contract(factory, call_data)
        pair_address = "0x" + raw[-40:]
        zero_addr = "0x" + "0" * 40

        if not raw or raw == "0x" or pair_address.lower() == zero_addr.lower():
            return None

        token0, token1 = _sort_tokens(token_a, token_b)
        return PairAddress(
            dex_name=dex_name,
            factory=factory,
            token0=token0,
            token1=token1,
            pair_address=pair_address,
        )
    except Exception as e:
        logger.debug("_fallback_get_pair failed for %s: %s", dex_name, e)
        return None
