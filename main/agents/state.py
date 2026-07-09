from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Status(StrEnum):
    PENDING = "PENDING"
    FILTERED = "FILTERED"
    AUDITED = "AUDITED"
    VALIDATED = "VALIDATED"
    AUTHORIZED = "AUTHORIZED"
    ABORTED = "ABORTED"
    EXECUTED = "EXECUTED"


class FleaMarketTarget(BaseModel):
    token_address: str
    quote_address: str
    pool_address: str
    dex_venue_name: str
    initial_liquidity_usd: float
    factory_address: str = ""
    token_age_hours: float = 0.0


class IngestionPayload(BaseModel):
    run_id: str
    token_address: str
    pool_address: str
    liq_usd: float
    is_verified: bool
    gross_spread_pct: float
    trade_size_usd: float
    pool_reserve_usd: float
    buy_router: str = ""
    sell_router: str = ""


class AuditResult(BaseModel):
    is_safe: bool
    threats: list[str]


class ArbitrageState(BaseModel):
    run_id: str
    token_address: str
    pool_address: str

    liq_usd: float
    is_verified: bool
    gross_spread_pct: float
    trade_size_usd: float
    pool_reserve_usd: float
    gas_usd: float = Field(default=0.02)

    minified_source: str | None = None
    audit_is_safe: bool | None = None
    audit_threats: list[str] | None = None

    expected_slippage_pct: float | None = None
    net_profit_usd: float | None = None

    tx_hash: str | None = None
    dry_run: bool = True
    simulated_receipt: dict[str, object] | None = None

    buy_router: str = ""
    sell_router: str = ""

    status: Status = Status.PENDING
    reason: str | None = None
