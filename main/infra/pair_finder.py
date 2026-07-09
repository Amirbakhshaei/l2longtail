from __future__ import annotations

import logging
from dataclasses import dataclass

from config.constants import (
    CAMELOT_V2_FACTORY,
    DEX_ROUTERS,
    SUSHISWAP_FACTORY,
    TRADER_JOE_FACTORY,
    UNISWAP_V2_FACTORY,
)
from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

GET_PAIR_SELECTOR = "0xe6a43905"

FACTORY_MAP = {
    "uniswap_v2": UNISWAP_V2_FACTORY,
    "sushiswap": SUSHISWAP_FACTORY,
    "camelot_v2": CAMELOT_V2_FACTORY,
    "trader_joe": TRADER_JOE_FACTORY,
}


def _encode_get_pair(token_a: str, token_b: str) -> str:
    a = token_a.lower().replace("0x", "").zfill(64)
    b = token_b.lower().replace("0x", "").zfill(64)
    return f"{GET_PAIR_SELECTOR}{a}{b}"


@dataclass
class PairInfo:
    dex_name: str
    dex_router: str
    factory: str
    pair_address: str
    token0: str
    token1: str


class PairFinder:
    def __init__(self, rpc_manager: RPCManager) -> None:
        self.rpc = rpc_manager
        self._cache: dict[str, PairInfo | None] = {}

    def _cache_key(self, dex_name: str, token_a: str, token_b: str) -> str:
        tokens = sorted([token_a.lower(), token_b.lower()])
        return f"{dex_name}:{tokens[0]}:{tokens[1]}"

    async def find_pair(
        self, dex_name: str, token_a: str, token_b: str
    ) -> PairInfo | None:
        key = self._cache_key(dex_name, token_a, token_b)
        if key in self._cache:
            return self._cache[key]

        factory = FACTORY_MAP.get(dex_name)
        if not factory:
            logger.warning("No factory for DEX: %s", dex_name)
            self._cache[key] = None
            return None

        call_data = _encode_get_pair(token_a, token_b)
        try:
            raw = await self.rpc.call_contract(factory, call_data)
            pair_address = "0x" + raw[-40:]
            zero_addr = "0x" + "0" * 40
            if not raw or raw == "0x" or pair_address.lower() == zero_addr.lower():
                self._cache[key] = None
                return None

            pair_info = PairInfo(
                dex_name=dex_name,
                dex_router=DEX_ROUTERS.get(dex_name, ""),
                factory=factory,
                pair_address=pair_address,
                token0=token_a,
                token1=token_b,
            )
            self._cache[key] = pair_info
            return pair_info

        except Exception as e:
            logger.debug(
                "getPair failed for %s/%s on %s: %s",
                token_a[:10], token_b[:10], dex_name, e,
            )
            self._cache[key] = None
            return None

    async def find_all_pairs(
        self, token_a: str, token_b: str
    ) -> list[PairInfo]:
        pairs: list[PairInfo] = []
        for dex_name in DEX_ROUTERS:
            pair = await self.find_pair(dex_name, token_a, token_b)
            if pair:
                pairs.append(pair)
        return pairs
