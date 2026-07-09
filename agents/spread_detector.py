from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass

from agents.state import IngestionPayload
from config.settings import Settings
from infra.dexscreener import DexPair, DexScreenerClient
from infra.pool_fetcher import PoolFetcher
from infra.price_oracle import PriceOracle

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    token_address: str
    token_symbol: str
    dex_pairs: list[DexPair]
    opportunities: list[IngestionPayload]


class SpreadDetector:
    def __init__(
        self,
        settings: Settings,
        dexscreener: DexScreenerClient,
        pool_fetcher: PoolFetcher,
        price_oracle: PriceOracle,
    ) -> None:
        self.settings = settings
        self.dexscreener = dexscreener
        self.pool_fetcher = pool_fetcher
        self.price_oracle = price_oracle
        self._seen_tokens: set[str] = set()

    async def scan_cycle(self) -> list[IngestionPayload]:
        opportunities: list[IngestionPayload] = []

        pairs = await self.dexscreener.discover_long_tail_tokens(
            min_liq_usd=self.settings.min_liquidity_usd,
            max_liq_usd=self.settings.max_liquidity_usd,
        )

        logger.info("Discovered %d long-tail pairs", len(pairs))

        token_seen: dict[str, DexPair] = {}
        for pair in pairs:
            addr = pair.base_token_address
            if addr not in token_seen:
                token_seen[addr] = pair

        for token_addr, first_pair in token_seen.items():
            if token_addr in self._seen_tokens:
                continue
            self._seen_tokens.add(token_addr)

            token_symbol = first_pair.base_token_symbol

            best_pair = first_pair
            for pair in pairs:
                if (
                    pair.base_token_address == token_addr
                    and (pair.liquidity_usd or 0) > (best_pair.liquidity_usd or 0)
                ):
                    best_pair = pair

            if not best_pair.pair_address:
                continue

            pool = await self.pool_fetcher.fetch_pool_reserves(best_pair.pair_address)
            if not pool:
                continue

            pool_liquidity_usd = self.pool_fetcher.compute_pool_reserve_usd(
                pool, weth_price_usd=2200.0
            )
            if pool_liquidity_usd < self.settings.min_liquidity_usd:
                pool_liquidity_usd = best_pair.liquidity_usd or 0

            if pool_liquidity_usd < self.settings.min_liquidity_usd:
                continue

            opp = await self.price_oracle.find_best_spread(
                token_address=token_addr,
                token_symbol=token_symbol,
                pool_address=best_pair.pair_address,
                pool_liquidity_usd=pool_liquidity_usd,
            )

            if opp and opp.spread_pct >= self.settings.min_spread_pct:
                payload = IngestionPayload(
                    run_id=f"{token_symbol.lower()}-{int(_time.time())}",
                    token_address=token_addr,
                    pool_address=opp.pool_address,
                    liq_usd=pool_liquidity_usd,
                    is_verified=True,
                    gross_spread_pct=opp.spread_pct,
                    trade_size_usd=min(
                        self.settings.max_trade_size_usd,
                        pool_liquidity_usd * 0.1,
                    ),
                    pool_reserve_usd=pool_liquidity_usd,
                )
                opportunities.append(payload)

                logger.info(
                    "OPPORTUNITY: %s spread=%.2f%% buy@%s sell@%s liq=$%.0f",
                    token_symbol,
                    opp.spread_pct,
                    opp.buy_router,
                    opp.sell_router,
                    pool_liquidity_usd,
                )

        return opportunities

    def reset_seen_tokens(self) -> None:
        self._seen_tokens.clear()
