from __future__ import annotations

import logging
from dataclasses import dataclass

from infra.dexscreener import DexPair, DexScreenerClient
from infra.pair_finder import PairFinder
from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

WETH_ADDR = "0x82aF49447D8a07e3bd95bD0d56f35241523fBab1"
USDC_ADDR = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"


@dataclass
class DiscoveryResult:
    token_address: str
    token_symbol: str
    base_token: str
    base_symbol: str
    dexes: list[str]
    pair_addresses: dict[str, str]
    pool_liquidity_usd: float


class TokenDiscovery:
    def __init__(
        self,
        rpc_manager: RPCManager,
        pair_finder: PairFinder,
        dex_client: DexScreenerClient,
    ) -> None:
        self.rpc = rpc_manager
        self.pair_finder = pair_finder
        self.dex_client = dex_client

    async def discover(
        self,
        min_liq_usd: float = 500.0,
        max_liq_usd: float = 100000.0,
    ) -> list[DiscoveryResult]:
        v2_tokens = await self._get_sushiswap_v2_tokens()
        logger.info("SushiSwap V2 tokens: %d unique", len(v2_tokens))

        multi_dex_tokens: dict[str, dict[str, str]] = {}

        for token_addr, gecko_dex, gecko_pair, base_token, base_symbol in v2_tokens:
            weth_pairs = await self.pair_finder.find_all_pairs(token_addr, WETH_ADDR)
            usdc_pairs = await self.pair_finder.find_all_pairs(token_addr, USDC_ADDR)

            all_pairs = {}
            for p in weth_pairs:
                all_pairs[p.dex_name] = p.pair_address
            for p in usdc_pairs:
                key = f"{p.dex_name}_usdc"
                all_pairs[key] = p.pair_address

            dex_names = list(set(p.dex_name for p in weth_pairs + usdc_pairs))

            if len(dex_names) < 2:
                continue

            if token_addr not in multi_dex_tokens:
                multi_dex_tokens[token_addr] = {
                    "symbol": gecko_dex,
                    "base_token": base_token,
                    "base_symbol": base_symbol,
                }
            multi_dex_tokens[token_addr]["dexes"] = dex_names
            multi_dex_tokens[token_addr]["pairs"] = all_pairs
            multi_dex_tokens[token_addr]["gecko_pair"] = gecko_pair

        discovered: list[DiscoveryResult] = []
        for token_addr, info in multi_dex_tokens.items():
            if "dexes" not in info:
                continue
            dexes = info["dexes"]
            if len(dexes) < 2:
                continue

            gecko_pair = info.get("gecko_pair")
            liquidity = gecko_pair.liquidity_usd if gecko_pair else 0.0
            if liquidity and not (min_liq_usd <= liquidity <= max_liq_usd):
                continue

            discovered.append(DiscoveryResult(
                token_address=token_addr,
                token_symbol=info["symbol"],
                base_token=info["base_token"],
                base_symbol=info["base_symbol"],
                dexes=dexes,
                pair_addresses=info.get("pairs", {}),
                pool_liquidity_usd=liquidity or 0.0,
            ))

        discovered.sort(key=lambda t: t.pool_liquidity_usd)
        logger.info(
            "Cross-DEX discovery: %d tokens with 2+ DEX pairs",
            len(discovered),
        )
        return discovered

    async def _get_sushiswap_v2_tokens(
        self,
    ) -> list[tuple[str, str, DexPair, str, str]]:
        results: list[tuple[str, str, DexPair, str, str]] = []
        seen: set[str] = set()

        pages = [1, 2, 3]
        for page in pages:
            data = await self.dex_client._get(
                f"/networks/arbitrum/dexes/sushiswap_arbitrum/pools?page={page}&sort=created_at"
            )
            if not data:
                break

            if isinstance(data, dict):
                items = data.get("data", [])
            elif isinstance(data, list):
                items = data
            else:
                break

            pools = self.dex_client._parse_pools({"data": items})
            if not pools:
                break

            for pool in pools:
                token_addr = pool.base_token_address.lower()
                if token_addr in seen:
                    continue
                seen.add(token_addr)

                if token_addr in (WETH_ADDR.lower(), USDC_ADDR.lower()):
                    continue

                base_token = pool.quote_token_address or WETH_ADDR
                base_symbol = pool.quote_token_symbol or "WETH"

                results.append((
                    token_addr,
                    pool.base_token_symbol,
                    pool,
                    base_token,
                    base_symbol,
                ))

        return results
