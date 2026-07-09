from __future__ import annotations

import logging
from dataclasses import dataclass

from config.constants import DEX_ROUTERS, WETH_ADDRESS
from infra.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

GET_AMOUNTS_OUT_SELECTOR = "0xd06ca61f"
WETH_DECIMALS = 18


def _encode_get_amounts_out(amount_in: int, path: list[str]) -> str:
    selector = GET_AMOUNTS_OUT_SELECTOR
    amount_hex = format(amount_in, "064x")
    path_offset = "0000000000000000000000000000000000000000000000000000000000000040"
    path_length = format(len(path), "064x")
    path_encoded = ""
    for addr in path:
        path_encoded += addr.lower().replace("0x", "").zfill(64)
    return f"{selector}{amount_hex}{path_offset}{path_length}{path_encoded}"


@dataclass
class RouterQuote:
    router_name: str
    router_address: str
    amount_out: int
    price_per_token: float


@dataclass
class ArbitrageOpportunity:
    token_address: str
    token_symbol: str
    buy_router: str
    buy_router_address: str
    sell_router: str
    sell_router_address: str
    buy_price_usd: float
    sell_price_usd: float
    spread_pct: float
    pool_address: str
    pool_liquidity_usd: float


class PriceOracle:
    MIN_RESERVE_USD = 500.0

    def __init__(self, rpc_manager: RPCManager) -> None:
        self.rpc = rpc_manager

    async def get_amounts_out(
        self,
        router_address: str,
        amount_in_wei: int,
        path: list[str],
    ) -> int | None:
        try:
            call_data = _encode_get_amounts_out(amount_in_wei, path)
            raw = await self.rpc.call_contract(router_address, call_data)

            from web3 import Web3

            w3 = Web3()
            result = w3.codec.decode(["uint256[]"], bytes.fromhex(raw.replace("0x", "")))[0]
            return int(result[-1])
        except Exception as e:
            logger.debug("getAmountsOut failed for %s: %s", router_address, e)
            return None

    async def _check_pair_liquidity(
        self,
        router_address: str,
        token_address: str,
        quote_token: str,
    ) -> float:
        from config.constants import (
            CAMELOT_V2_FACTORY,
            DEX_ROUTERS,
            SUSHISWAP_FACTORY,
            UNISWAP_V2_FACTORY,
        )

        router_to_factory = {}
        for dex_name, factory_addr in [
            ("uniswap_v2", UNISWAP_V2_FACTORY),
            ("sushiswap", SUSHISWAP_FACTORY),
            ("camelot_v2", CAMELOT_V2_FACTORY),
        ]:
            r = DEX_ROUTERS.get(dex_name, "")
            if r:
                router_to_factory[r.lower()] = factory_addr

        factory = router_to_factory.get(router_address.lower())
        if not factory:
            return 0.0

        try:
            from infra.pair_finder import _encode_get_pair
            call_data = _encode_get_pair(token_address, quote_token)
            raw = await self.rpc.call_contract(factory, call_data)
            pair_addr = "0x" + raw[-40:]
            zero = "0x" + "0" * 40
            if pair_addr.lower() == zero.lower():
                return 0.0

            reserves_raw = await self.rpc.call_contract(pair_addr, "0x0902f1ac")
            data = bytes.fromhex(reserves_raw.replace("0x", ""))
            r0 = int.from_bytes(data[0:32], "big")
            r1 = int.from_bytes(data[32:64], "big")

            token0_raw = await self.rpc.call_contract(pair_addr, "0x0dfe1681")
            token0 = "0x" + token0_raw[-40:]

            if quote_token.lower() == token0.lower():
                quote_reserve = r0
            else:
                quote_reserve = r1

            from config.constants import WETH_ADDRESS
            if quote_token.lower() == WETH_ADDRESS.lower():
                return (quote_reserve / 1e18) * 3800.0
            else:
                return (quote_reserve / 1e6) * 1.0
        except Exception as e:
            logger.debug("_check_pair_liquidity failed: %s", e)
            return 0.0

    async def get_cross_dex_prices(
        self,
        token_address: str,
        quote_token: str = WETH_ADDRESS,
        amount_in_wei: int = 10**18,
    ) -> list[RouterQuote]:
        quotes: list[RouterQuote] = []

        for name, address in DEX_ROUTERS.items():
            liq_usd = await self._check_pair_liquidity(
                address, token_address, quote_token
            )
            if liq_usd < self.MIN_RESERVE_USD:
                continue

            amount_out = await self.get_amounts_out(
                address, amount_in_wei, [quote_token, token_address]
            )
            if amount_out and amount_out > 0:
                quotes.append(
                    RouterQuote(
                        router_name=name,
                        router_address=address,
                        amount_out=amount_out,
                        price_per_token=float(amount_out),
                    )
                )

        return quotes

    async def find_best_spread(
        self,
        token_address: str,
        token_symbol: str,
        pool_address: str,
        pool_liquidity_usd: float,
        quote_token: str = WETH_ADDRESS,
    ) -> ArbitrageOpportunity | None:
        quotes = await self.get_cross_dex_prices(token_address, quote_token)

        if len(quotes) < 2:
            return None

        best_buy = min(quotes, key=lambda q: q.price_per_token)
        best_sell = max(quotes, key=lambda q: q.price_per_token)

        if best_buy.router_name == best_sell.router_name:
            return None

        spread_pct = (
            (best_sell.price_per_token - best_buy.price_per_token)
            / best_buy.price_per_token
        ) * 100

        if spread_pct <= 0:
            return None

        return ArbitrageOpportunity(
            token_address=token_address,
            token_symbol=token_symbol,
            buy_router=best_buy.router_name,
            buy_router_address=best_buy.router_address,
            sell_router=best_sell.router_name,
            sell_router_address=best_sell.router_address,
            buy_price_usd=best_buy.price_per_token,
            sell_price_usd=best_sell.price_per_token,
            spread_pct=spread_pct,
            pool_address=pool_address,
            pool_liquidity_usd=pool_liquidity_usd,
        )
