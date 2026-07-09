# `revision.md` - L2 Flea Market Arbitrage Architecture (v2.0)

**Lead Architect:** Amirhossein Bakhshaei
**Target Network:** Arbitrum One (L2)
**Orchestration:** LangGraph (Asynchronous Python)

## 1. Architectural Paradigm Shift: The Dual-Process System
The primary vulnerability of the v1.0 architecture was latency. Placing an LLM in the critical path between opportunity discovery and execution guarantees that MEV bots will front-run the trade. 

Version 2.0 decouples the workflow into two parallel, asynchronous processes. 
* **Process A (The Indexer):** Heavy, slow, and analytical. Runs in the background to build a whitelist.
* **Process B (The Sniper):** Ultra-fast, deterministic, and mathematical. Runs in milliseconds to execute trades.

---

## 2. Process A: The Background Indexer (Security & Discovery)
This process operates independently of live price action. Its sole job is to find new tokens, audit their smart contracts, and build a local database of safe, tradable assets.

### Node A1: Factory Scanner
* **Role:** Websocket listener targeting L2 factory contracts.
* **Action:** Subscribes to `PairCreated` (Uniswap V2 clones, SushiSwap) and `PoolCreated` (Uniswap V3, Camelot V3).
* **Output:** Extracts raw token addresses and passes them to the filter.

### Node A2: Preliminary Filter Gate
* **Role:** Deterministic garbage collection.
* **Action:** Queries the blockchain for contract verification status and initial liquidity bounds.
* **Routing:** IF unverified OR liquidity < 2500 USD, drop immediately. IF valid, route to LLM Auditor.

### Node A3: LLM Security Auditor
* **Role:** Deep-scan smart contract forensics.
* **Action:** Minifies the verified Solidity code and passes it to the Gemini reasoning API. Looks for hidden transfer taxes, malicious mints, and `emergencyWithdraw` traps.
* **Routing:** IF `is_safe == True`, route to Database Manager.

### Node A4: Database Manager (SQLite)
* **Role:** High-speed local state persistence.
* **Action:** Caches the safe token address, its specific pool addresses across DEXs, and its fee tiers into a local SQLite database (`cleared_tokens.db`). 

---

## 3. Process B: The Execution Loop (Latency Critical)
This process runs continuously on a microsecond loop. It only looks at tokens that have already been cleared by Process A. **No external API calls or LLM invocations exist in this loop.**

### Node B1: Price Anomaly Scanner
* **Role:** The trigger mechanism.
* **Action:** Tracks the reserves and ticks of all tokens inside `cleared_tokens.db` locally. 
* **Routing:** IF a cross-DEX price spread > 2.0% is detected, initialize the `ArbitrageState` and route to Quant Analyst.

### Node B2: Quant Analyst (Precision Math)
* **Role:** Calculates exact net yields using constant product formulas rather than rough percentage approximations.
* **V2 Action:** Calculates exact output amount ($\Delta y$) given an input amount ($\Delta x$), incorporating the standard 0.3% fee:
  $$\Delta y = \frac{y \cdot \Delta x \cdot 0.997}{x + \Delta x \cdot 0.997}$$
* **V3 Action:** Extracts current `slot0` (sqrtPriceX96) and current active `liquidity` to calculate the exact marginal price impact across tick boundaries.
* **Net Calculation:** $$\text{Net Profit USD} = (\Delta y \cdot \text{Price}_y) - \text{Gas Overhead}$$
* **Routing:** IF Net Profit USD $\ge$ 0.50, route to Execution Gatekeeper. ELSE, abort.

### Node B3: Execution Gatekeeper & Builder
* **Role:** The final MEV-shielded trigger.
* **Action:** Constructs the transaction calldata. Enforces strict `amountOutMinimum` parameters to protect against micro-block state changes.
* **Broadcast:** Signs the bundle locally and broadcasts via private RPC relays (Flashbots/Builder endpoints) to completely bypass the public mempool.

---

## 4. Engine Upgrade Specifications

### Removing Redundant RPC Calls
The `getPair()` and `getAmountsOut()` router calls are entirely deprecated. 
* V2 Pair addresses must be calculated locally using the `CREATE2` deterministic opcode formula.
* Cross-DEX pricing must be simulated entirely within the local Python environment using the reserves fetched by the WebSocket stream.

### DEX Compatibility Matrix
The system now natively supports both constant product (V2) and concentrated liquidity (V3) mechanics, reflecting the true distribution of volume on Arbitrum.

| DEX Protocol | Factory Event | Pricing Metric | Math Model |
| :--- | :--- | :--- | :--- |
| **SushiSwap V2** | `PairCreated` | `getReserves()` | $x \cdot y = k$ |
| **Uniswap V3** | `PoolCreated` | `slot0` / `liquidity` | Concentrated Ticks |
| **Camelot V3** | `PoolCreated` | `slot0` / `liquidity` | Concentrated Ticks |

---

## 5. Execution State Schema (Process B)
The streamlined typed dictionary for the Execution Loop. 

```python
from typing import TypedDict, Literal

class ExecutionState(TypedDict):
    trade_id: str
    token_address: str
    buy_pool: str
    sell_pool: str
    spread_pct: float
    trade_size_usd: float
    exact_output_usd: float
    gas_overhead_usd: float
    net_profit_usd: float
    status: Literal["PENDING", "EXECUTED", "ABORTED"]
    abort_reason: str