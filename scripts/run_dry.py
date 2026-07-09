import asyncio

from agents.ingestion_node import create_state_from_payload
from agents.state import IngestionPayload
from config.settings import Settings
from db.blacklist import BlacklistDB
from db.cache import ContractCache
from graph.pipeline import compile_graph
from monitoring.logger import setup_logger

ALPHA_PAYLOAD = IngestionPayload(
    run_id="alpha-001",
    token_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    liq_usd=50000.0,
    is_verified=True,
    gross_spread_pct=8.5,
    trade_size_usd=200.0,
    pool_reserve_usd=50000.0,
)

BETA_PAYLOAD = IngestionPayload(
    run_id="beta-001",
    token_address="0x0000000000000000000000000000000000000001",
    pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    liq_usd=50000.0,
    is_verified=True,
    gross_spread_pct=12.0,
    trade_size_usd=200.0,
    pool_reserve_usd=50000.0,
)

GAMMA_PAYLOAD = IngestionPayload(
    run_id="gamma-001",
    token_address="0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
    pool_address="0xDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD",
    liq_usd=10000.0,
    is_verified=True,
    gross_spread_pct=15.0,
    trade_size_usd=100.0,
    pool_reserve_usd=10000.0,
)

DELTA_PAYLOAD = IngestionPayload(
    run_id="delta-001",
    token_address="0xEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
    pool_address="0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
    liq_usd=3000.0,
    is_verified=True,
    gross_spread_pct=3.0,
    trade_size_usd=500.0,
    pool_reserve_usd=3000.0,
)

UNVERIFIED_PAYLOAD = IngestionPayload(
    run_id="unverified-001",
    token_address="0x1111111111111111111111111111111111111111",
    pool_address="0x2222222222222222222222222222222222222222",
    liq_usd=50000.0,
    is_verified=False,
    gross_spread_pct=10.0,
    trade_size_usd=100.0,
    pool_reserve_usd=50000.0,
)


async def main() -> None:
    settings = Settings(dry_run=True)
    setup_logger(settings.log_level)

    cache = ContractCache(settings.db_path)
    blacklist_db = BlacklistDB(settings.db_path)

    app = await compile_graph(settings, cache, blacklist_db)

    test_profiles = [
        ("ALPHA (Happy Path)", ALPHA_PAYLOAD),
        ("BETA (Blacklisted)", BETA_PAYLOAD),
        ("GAMMA (Honeypot)", GAMMA_PAYLOAD),
        ("DELTA (Slippage Decay)", DELTA_PAYLOAD),
        ("UNVERIFIED", UNVERIFIED_PAYLOAD),
    ]

    for label, payload in test_profiles:
        state = create_state_from_payload(payload, dry_run=True)
        config = {"configurable": {"thread_id": payload.run_id}}

        result = await app.ainvoke(state.model_dump(), config=config)

        print(f"\n{'=' * 70}")
        print(f"  [{label}]  run_id={result['run_id']}")
        print(f"  Status:      {result['status']}")
        print(f"  Reason:      {result.get('reason', 'N/A')}")
        print(f"  Net Profit:  ${result.get('net_profit_usd', 'N/A')}")
        print(f"  Slippage:    {result.get('expected_slippage_pct', 'N/A')}%")
        print(f"  TX Hash:     {result.get('tx_hash', 'N/A')}")
        print(f"  Dry Run:     {result.get('dry_run', 'N/A')}")
        print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
