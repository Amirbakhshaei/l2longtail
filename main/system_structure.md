# SYSTEM CORRECTION & ARCHITECTURAL REFACTORING MANDATE

## [CRITICAL ERROR REVIEW]
During your last development loop, you encountered an Alchemy RPC `400 Bad Request` error when querying `eth_getLogs` for factory events. To bypass this, you implemented a shortcut that completely violated the core trading strategy:
1. You wrote a seed script (`scripts/seed_cleared_db.py`) that manually populated the tracking system with high-market-cap, major liquid assets (ARB, WBTC, LINK, MIM, SUSHI, GMX, MAGIC, RDNT, DAI, USDC).
2. You pointed your GeckoTerminal backup scraper to fetch pools sorted by `h24_tx_count_desc`.
3. This caused the system to paper-trade the most highly optimized institutional HFT slots (DAI, ARB, WBTC) instead of the long-tail flea market.

You are required to perform a full codebase refactor immediately to remove these structural violations and safely operate within free-tier infrastructure bounds.

---

## ## Phase 1: Pure Deletion & Strategy Restoration
* **Action:** Permanently delete `scripts/seed_cleared_db.py` from the project workspace. 
* **Action:** Open `orchestrator.py` and strip out any logic references importing, executing, or depending on `seed_cross_dex_tokens`. The tracking layer database must start completely empty and rely purely on true real-time programmatic discovery.

---

## ## Phase 2: Fixing the Alchemy RPC "400 Bad Request"
The `400 Bad Request` occurs because free-tier Alchemy nodes reject massive historic event log scans (`lookback_blocks=50000`) over public factory contract topics. You must update your polling and streaming layers to eliminate this error:
* **Option A (Preferred):** Force your ingestion node (`infra/websocket_listener.py`) to bypass raw block historical lookbacks entirely. Rely strictly on an active async WebSocket connection streaming live incoming block logs via `eth_subscribe` to the `logs` topic.
* **Option B (Fallback):** If historical polling is used, hardcode an absolute maximum block lookback depth of `1,000` blocks per cycle to keep the payload size safely within free-tier limits.

---

## ## Phase 3: Implementing the Hard Asset Boundary (Blacklist)
You must enforce an ironclad edge gate filter. Open your target token processing loops and implement this precise verification check:

```python
MAJOR_ASSET_BLACKLIST = {
    "0x82af49447d8a07e3bd95bd0d56f35241523fab1",  # WETH
    "0x912ce59144191c1204e64559fe8253a0e49e6548",  # ARB
    "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f",  # WBTC
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # USDC
    "0xfd086bc7cd5c481dcc9c85ebe478a1d0b69c45a1",  # USDT
    "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1",  # DAI
    "0xf97f4df75117a78c1a5a0dbb814af92458539fb4",  # LINK
}

# Strictly reject major pairs to stay completely out of HFT crossfire
if token_address.lower() in MAJOR_ASSET_BLACKLIST:
    logger.info(f"STRATEGY BLOCK: Skipping major liquid asset {token_address}")
    return
##Phase 4: Long-Tail Sorting OptimizationIf a data aggregator or GeckoTerminal fallback query is executed, you are explicitly forbidden from using high-volume filter queries like sort=h24_tx_count_desc.Modify the fallback API ingestion logic to query strictly for newly indexed items sorted by discovery time (sort=created_at or pool creation arrays).Apply a strict upper bound safety gate check: if an asset's total liquidity parameter ($\text{liq\_usd}$) is greater than $100,000.00, drop the opportunity immediately. We are only hunting the inefficient, early-stage mispricings of exotic, micro-cap pools.
     
# Step-by-Step Refactoring InstructionsRemove all trace code files or internal variables related to the artificial major token seeds.Adjust the log polling block range limits or prioritize the raw socket log subscription streams to completely resolve the Alchemy status 400 crashes.Embed the MAJOR_ASSET_BLACKLIST validation script directly into the ingestion filter layer.Execute the system check suite over ruff/lint to verify that all modules compile cleanly with zero errors.Provide a confirmation update once the codebase matches these strategic parameters exactly. Do not use shortcuts.