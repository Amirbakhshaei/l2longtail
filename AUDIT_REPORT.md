# Long-Tail Arbitrage System — Audit Report

**Date:** 2026-07-10
**Dry Run Session:** 2026-07-09 12:40:46 — 2026-07-09 17:09:04 (4h 28m)
**Network:** Arbitrum One (Chain ID 42161)
**Mode:** PAPER (dry_run=true)

---

## 1. Dry Run Session Summary

### Startup Parameters
| Parameter | Value |
|-----------|-------|
| `DRY_RUN` | `true` |
| `TRADE_SIZE_USD` | `$200` |
| `MIN_SPREAD_PCT` | `2.0%` |
| `SCAN_INTERVAL_A` | `30s` |
| `SCAN_INTERVAL_B` | `1s` |
| `gas_usd` baseline | `$0.02` |
| `min_net_profit_usd` | `$0.50` |
| `MIN_LIQUIDITY_USD` | `$2,500` |
| `MAX_LIQUIDITY_USD` | `$100,000` |
| `MAX_TOKEN_AGE_HOURS` | `72h` |

### Session Outcome
| Metric | Value |
|--------|-------|
| Process A scan cycles | ~540 (every 30s for 4.5h) |
| New targets discovered | **0** |
| Tokens cleared to DB | **0** |
| Process B scan cycles | **0** (no cleared tokens to scan) |
| Trades executed | **0** |
| Trades aborted | **0** |

### Root Cause: Zero Targets Discovered
Process A consistently returned `Found 0 new targets` on every cycle. The discovery pipeline (`FleaMarketDiscovery.scan_recent_pairs`) failed to find any new token pairs due to **RPC errors** (see Issue #1 and #2 below).

---

## 2. System Architecture

### 2.1 Two-Process Runtime Topology

```
┌─────────────────────────────────────────────────────────────────────┐
│                         app.py (Gradio UI)                         │
│                    HF ZeroGPU Spaces Dashboard                     │
│                    Daemon thread → asyncio loop                     │
└──────────────────────┬──────────────────────────┬───────────────────┘
                       │                          │
           ┌───────────▼──────────┐  ┌────────────▼──────────────┐
           │   Process A (Indexer) │  │  Process B (Sniper)       │
           │   ~30s scan interval  │  │  ~1s scan interval        │
           │   Background/Analytical│  │  Foreground/Latency-critical│
           └───────────┬──────────┘  └────────────┬──────────────┘
                       │                          │
         ┌─────────────▼──────────────┐           │
         │  FleaMarketDiscovery        │           │
         │  scan_recent_pairs()        │           │
         │  eth_getLogs → parse → gate │           │
         └─────────────┬──────────────┘           │
                       │                          │
         ┌─────────────▼──────────────┐           │
         │  Filter Gate                │           │
         │  blacklist + liq bounds     │           │
         └─────────────┬──────────────┘           │
                       │                          │
         ┌─────────────▼──────────────┐           │
         │  SourceFetcher (Etherscan)  │           │
         │  → minify_solidity()        │           │
         │  → LLMSecurityAuditor (Groq)│           │
         └─────────────┬──────────────┘           │
                       │                          │
         ┌─────────────▼──────────────┐           │
         │  CREATE2 pair computation   │           │
         │  uniswap_v2, sushiswap,     │           │
         │  camelot_v2                 │           │
         └─────────────┬──────────────┘           │
                       │                          │
         ┌─────────────▼──────────────┐           │
         │  ClearedTokensDB (SQLite)   │◄──────────┤
         │  /data/cleared_tokens.db    │  reads    │
         └─────────────────────────────┘           │
                                                   │
         ┌─────────────────────────────────────────▼──┐
         │  PriceAnomalyScanner                       │
         │  fetch reserves → find_local_spreads()     │
         └─────────────┬──────────────────────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  QuantAnalyst               │
         │  compute_v2_output() × 2    │
         │  compute_net_profit()       │
         │  floor: net >= $0.50        │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  ExecutionGatekeeper        │
         │  DRY: mark EXECUTED         │
         │  LIVE: build+sign+submit TX │
         └─────────────────────────────┘
```

### 2.2 LangGraph DAG (Defined but Unused at Runtime)

A second architecture exists in `graph/pipeline.py` using LangGraph `StateGraph`. It defines a formal DAG:

```
ingestion → filter_gate → rag_auditor → slippage_analyst → execution → terminal
```

Each node uses `route_on_status()` to short-circuit to `terminal` on `ABORTED`.

**This DAG is NOT wired into `orchestrator.py` or `app.py`.** The runtime uses `ProcessAIndexer` + `ProcessBSniper` directly. The LangGraph nodes in `agents/` are dead code.

### 2.3 State Models

**Runtime State (`process_b_sniper.py:31` — `ExecutionState` TypedDict):**
```python
{
    trade_id, token_address, buy_pool, sell_pool, buy_dex, sell_dex,
    spread_pct, trade_size_usd, exact_output_usd, gas_overhead_usd,
    net_profit_usd, status: "PENDING"|"EXECUTED"|"ABORTED", abort_reason
}
```

**LangGraph State (`agents/state.py:46` — `ArbitrageState` Pydantic):**
```python
{
    run_id, token_address, pool_address, liq_usd, is_verified,
    gross_spread_pct, trade_size_usd, pool_reserve_usd, gas_usd,
    minified_source, audit_is_safe, audit_threats,
    expected_slippage_pct, net_profit_usd, tx_hash, dry_run,
    simulated_receipt, buy_router, sell_router,
    status: Status(enum), reason
}
```

---

## 3. File Path Map

### 3.1 Entry Points

| File | Lines | Role |
|------|-------|------|
| `app.py` | 220 | Gradio UI dashboard + daemon engine thread (HF ZeroGPU) |
| `orchestrator.py` | 184 | CLI-based dual-process orchestrator |

### 3.2 Process A — Background Indexer

| File | Lines | Role |
|------|-------|------|
| `process_a_indexer.py` | 233 | `ProcessAIndexer` class — scan cycle, filter gate, LLM audit, DB upsert |

### 3.3 Process B — Execution Sniper

| File | Lines | Role |
|------|-------|------|
| `process_b_sniper.py` | 389 | `ProcessBSniper` — `PriceAnomalyScanner`, `QuantAnalyst`, `ExecutionGatekeeper` |

### 3.4 Agents (LangGraph Nodes — Unused at Runtime)

| File | Lines | Role |
|------|-------|------|
| `agents/state.py` | 73 | `ArbitrageState`, `FleaMarketTarget`, `Status` enum |
| `agents/minifier.py` | 55 | `minify_solidity()` — strip comments, collapse whitespace |
| `agents/filter_gate_node.py` | 56 | Filter gate node (LangGraph) |
| `agents/rag_auditor_node.py` | 207 | LLM auditor node with retry/fallback (LangGraph) |
| `agents/slippage_analyst_node.py` | 50 | Slippage math node (LangGraph) |
| `agents/execution_node.py` | 144 | Execution node with TX build/sign (LangGraph) |
| `agents/spread_detector.py` | 120 | DexScreener-based spread scanner (LangGraph ingestion) |
| `agents/ingestion_node.py` | — | Ingestion node (LangGraph) |

### 3.5 Infrastructure

| File | Lines | Role |
|------|-------|------|
| `infra/rpc_manager.py` | 99 | `RPCManager` — primary/fallback RPC with failover |
| `infra/rate_limiter.py` | 28 | `TokenBucketRateLimiter` — async token bucket |
| `infra/flea_market_discovery.py` | 275 | `FleaMarketDiscovery` — PairCreated event scanner |
| `infra/source_fetcher.py` | 59 | `SourceFetcher` — Etherscan V2 contract source |
| `infra/pool_fetcher.py` | 125 | `PoolFetcher` — reserve/token/decimal fetcher |
| `infra/local_pricing.py` | 223 | V2 constant-product + V3 concentrated liquidity math |
| `infra/create2.py` | 160 | Deterministic V2 pair address computation |
| `infra/live_executor.py` | 268 | `LiveExecutor` — calldata encoding, TX signing, submission |
| `infra/dexscreener.py` | — | DexScreener API client |
| `infra/price_oracle.py` | — | Price oracle |
| `infra/keystore.py` | — | Private key management |
| `infra/multicall.py` | — | Multicall3 batched RPC |
| `infra/pair_finder.py` | — | getPair() fallback |
| `infra/v3_pool.py` | — | V3 pool state reader |
| `infra/token_discovery.py` | — | Token metadata |
| `infra/websocket_listener.py` | — | WebSocket event listener |

### 3.6 Database

| File | Lines | Role |
|------|-------|------|
| `db/cleared_tokens.py` | 177 | `ClearedTokensDB` — SQLite upsert/query for safe tokens |
| `db/cache.py` | 155 | `ContractCache` — source cache, audit cache, execution log, flea cache |
| `db/blacklist.py` | — | Token blacklist DB |
| `db/schema.sql` | — | SQLite DDL |

### 3.7 Configuration

| File | Lines | Role |
|------|-------|------|
| `config/factories.py` | 73 | `FACTORY_REGISTRY`, `MAJOR_ASSET_BLACKLIST`, liquidity bounds |
| `config/constants.py` | 73 | Chain IDs, router addresses, ABIs |
| `config/settings.py` | — | Pydantic settings |
| `config/blacklist.json` | — | Seed blacklist |

### 3.8 Monitoring

| File | Lines | Role |
|------|-------|------|
| `monitoring/alerts.py` | 117 | `TelegramAlerts` — formatted notifications |
| `monitoring/metrics.py` | — | Prometheus metrics |
| `monitoring/logger.py` | — | Structured logging |

### 3.9 Graph (LangGraph)

| File | Lines | Role |
|------|-------|------|
| `graph/pipeline.py` | 110 | `build_pipeline()` — LangGraph StateGraph construction |
| `graph/checkpoint.py` | — | Graph checkpointing |

### 3.10 Scripts

| File | Lines | Role |
|------|-------|------|
| `scripts/run_dry.py` | — | Dry run script |
| `scripts/run_dry_1h.py` | — | 1-hour dry run |
| `scripts/run_live.py` | — | Live execution |
| `scripts/run_real_test.py` | — | Real test |
| `scripts/run_scanner.py` | — | Scanner-only |
| `scripts/seed_blacklist.py` | — | Blacklist seeder |

---

## 4. Workflow Logic — End-to-End

### 4.1 Process A: Discovery → Audit → Clear

```
Step 1: FleaMarketDiscovery.scan_recent_pairs(lookback_blocks=1000)
  ├── For each factory in FACTORY_REGISTRY (Camelot V2, SushiSwap V2, Uniswap V2):
  │     ├── eth_getLogs(fromBlock, toBlock, factory_address, PairCreated topic)
  │     ├── Parse log → extract token0, token1, pool_address
  │     ├── _classify_pair(): one side must be major asset, other exotic
  │     ├── _passes_all_gates():
  │     │     ├── cache.is_known_bad_token() → skip if known bad
  │     │     ├── _estimate_liquidity(pair) → skip if < $2500 or > $100K
  │     │     └── _check_token_age() → skip if > 72h
  │     └── Return FleaMarketTarget
  └── Return list[FleaMarketTarget]

Step 2: For each target:
  ├── _passes_filter_gate(): blacklist + liq bounds (redundant with Step 1)
  ├── SourceFetcher.fetch_source(token_address) → Etherscan V2 API
  ├── minify_solidity(source) → strip comments, collapse whitespace
  ├── LLMSecurityAuditor.audit(minified) → Groq API (llama-3.3-70b-versatile)
  │     ├── Check 5 vulnerability classes
  │     └── Return AuditResult(is_safe, threats)
  ├── If audit passes: compute_v2_pair_address() for 3 DEXes
  └── ClearedTokensDB.upsert_token() × 3
```

### 4.2 Process B: Scan → Evaluate → Execute

```
Step 1: PriceAnomalyScanner.scan()
  ├── Get all cleared tokens from DB
  ├── Fetch WETH/USD price from WETH/USDC pool reserves
  ├── For each token with ≥2 DEX pairs:
  │     ├── Fetch reserves for each pair (with 5s cache)
  │     ├── find_local_spreads(): compute V2 output for all DEX pairs
  │     │     ├── buy: reserve_in[1] → reserve_out[0] (1 ETH → tokens)
  │     │     ├── sell: reserve_in[0] → reserve_out[1] (tokens → ETH)
  │     │     └── spread = (sell_out - buy_in) / buy_in × 100
  │     └── Filter: spread >= min_spread_pct (2.0%)
  └── Return sorted opportunities (highest spread first)

Step 2: QuantAnalyst.evaluate(opportunity)
  ├── buy_amount_in = (trade_size_usd / weth_price) × 1e18
  ├── amount_out = compute_v2_output(buy reserves, buy_amount_in)
  ├── sell_amount_out = compute_v2_output(sell reserves, amount_out)
  ├── output_usd = (sell_amount_out / 1e18) × weth_price
  ├── net_profit = (spread_pct / 100) × trade_size_usd - gas_usd
  └── Return ExecutionState (PENDING if profitable, ABORTED if not)

Step 3: ExecutionGatekeeper.execute(state)
  ├── If ABORTED → return
  ├── If net_profit < $0.50 → ABORT
  ├── If dry_run → mark EXECUTED, log paper trade
  └── If live → build calldata, sign TX, submit via private RPC
```

---

## 5. Configuration Reference

### 5.1 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `true` | Paper trading mode |
| `ALCHEMY_API_KEY` | (from .env) | Alchemy RPC API key |
| `LLM_API_KEY` | (from .env) | Groq/OpenRouter LLM API key |
| `LLM_MODEL_PRIMARY` | `deepseek/deepseek-chat-v3-0324:free` | Primary LLM model |
| `LLM_MODEL_FALLBACK` | `qwen/qwen3-235b-a22b:free` | Fallback LLM model |
| `TRADE_SIZE_USD` | `200` | Trade size in USD |
| `MIN_SPREAD_PCT` | `2.0` | Minimum spread % to execute |
| `SCAN_DURATION` | `300` | Duration for CLI orchestrator |
| `SCAN_INTERVAL_A` | `30` | Process A scan interval (seconds) |
| `SCAN_INTERVAL_B` | `1` | Process B scan interval (seconds) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `ETHERSCAN_API_KEY` | (missing from .env) | Etherscan V2 API key |
| `TELEGRAM_BOT_TOKEN` | (empty) | Telegram bot token |
| `TELEGRAM_CHAT_ID` | (empty) | Telegram chat ID |
| `KEYSTORE_PATH` | `./keystore.json` | Wallet keystore path |
| `KEYSTORE_PASSPHRASE` | `your_passphrase_here` | Keystore passphrase |

### 5.2 Hardcoded Thresholds

| Constant | Value | Location |
|----------|-------|----------|
| `MIN_LIQUIDITY_USD` | `$2,500` | `config/factories.py:26` |
| `MAX_LIQUIDITY_USD` | `$100,000` | `config/factories.py:27` |
| `MAX_TOKEN_AGE_HOURS` | `72` | `config/factories.py:28` |
| `gas_usd` | `$0.02` | `orchestrator.py:114`, `app.py:106` |
| `min_net_profit_usd` | `$0.50` | `process_b_sniper.py:254`, `agents/slippage_analyst_node.py:27` |
| `V2_FEE` | `0.997` (0.3%) | `infra/local_pricing.py:16` |
| `CACHE_DURATION` | `5s` (reserves) | `process_b_sniper.py:77` |
| `WETH_PRICE_CACHE` | `60s` | `process_b_sniper.py:80` |
| `lookback_blocks` | `1000` (~33min) | `process_a_indexer.py:144` |

### 5.3 Blacklisted Addresses (Major Assets)

| Token | Address |
|-------|---------|
| WETH | `0x82af49447d8a07e3bd95bd0d56f35241523fab1` |
| WBTC | `0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f` |
| USDC | `0xaf88d065e77c8cc2239327c5edb3a432268e5831` |
| USDT | `0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9` |
| DAI | `0xda10009cbd5d07dd0cecc66161fc93d7c9000da1` |
| ARB | `0x912ce59144191c1204e64559fe8253a0e49e6548` |
| LINK | `0xf97f4df75117a78c1a5a0dbb814af92458539fb4` |
| UNI | `0xfa9fa403952bf6964d4469a7ebbe16ac158aed17` |

### 5.4 Factory Registry

| DEX | Factory Address | Event Topic | Version |
|-----|----------------|-------------|---------|
| Camelot V2 | `0x6EcCab422D763aC031210895C81787E87B43A652` | `0x0d3648...` | v2 |
| SushiSwap V2 | `0xc35DADB65012eC5796536bD9864eD8773aBc74C4` | `0x0d3648...` | v2 |
| Uniswap V2 | `0xf1d7cc64fb4452f05c498126312ebe29f30fbcf9` | `0x0d3648...` | v2 |

### 5.5 DEX Router Addresses

| DEX | Router |
|-----|--------|
| Uniswap V2 | `0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24` |
| SushiSwap | `0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506` |
| Camelot V2 | `0xc873fEcbd354f5A56E00E710B90EF1836D620000` |
| Trader Joe | `0x7BFdb40e7c1B2A47aF4E7008bC2b1a2b5D7F0b7c` |

### 5.6 RPC Endpoints

| Type | URL |
|------|-----|
| Primary | `https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}` |
| Fallback | `https://arb1.arbitrum.io/rpc` |
| Bundle Relay | (empty — not configured) |

---

## 6. Issues Found

### Issue #1 — CRITICAL: Alchemy RPC API Key Invalid

**Severity:** CRITICAL
**Impact:** Primary RPC completely non-functional; all `eth_getLogs` calls fail

**Evidence (log.txt):**
```
Line 9:   POST https://arb-mainnet.g.alchemy.com/v2/BSHekkDdRZe1ZtXm7u1hd "HTTP/1.1 400 Bad Request"
Line 11:  POST https://arb-mainnet.g.alchemy.com/v2/BSHekkDdRZe1ZtXm7u1hd "HTTP/1.1 400 Bad Request"
Line 13:  POST https://arb-mainnet.g.alchemy.com/v2/BSHekkDdRZe1ZtXm7u1hd "HTTP/1.1 400 Bad Request"
Line 14:  RPC failover toggled, active=True
Line 16:  scan_recent_pairs failed for Uniswap V2: Client error '400 Bad Request'
```

**Root Cause:** The Alchemy API key `BSHekkDdRZe1ZtXm7u1hd` is invalid, expired, or rate-limited. Every request returns HTTP 400.

**Consequence:** After 3 consecutive failures, `RPCManager` toggles failover to the public Arbitrum RPC (`arb1.arbitrum.io/rpc`). However, the `eth_getLogs` calls to scan factory events may still fail on the public endpoint due to response size limits or rate limiting.

**Fix:**
1. Regenerate a valid Alchemy API key and update `ALCHEMY_API_KEY` in `.env`
2. Consider adding a startup health check that validates the RPC key before entering the main loop

---

### Issue #2 — HIGH: Public RPC Rate Limiting (429) with No Backoff

**Severity:** HIGH
**Impact:** Discovery scan cycles fail silently when rate-limited

**Evidence (log.txt):**
```
Line 371: POST https://arb1.arbitrum.io/rpc "HTTP/1.1 429 Too Many Requests"
Line 372: POST https://arb-mainnet.g.alchemy.com/v2/... "HTTP/1.1 400 Bad Request"
Line 373: scan_recent_pairs failed for Uniswap V2: Client error '400 Bad Request'
```

**Root Cause:** `RPCManager.call()` (`infra/rpc_manager.py:48-65`) catches `HTTPStatusError` and retries once on the alternate endpoint, but has:
- No exponential backoff
- No rate-limit-aware delay (429 responses)
- The failover toggle resets `_consecutive_failures` to 0 on success, meaning a single success after 3 failures permanently switches endpoints

**Fix:**
1. Add exponential backoff on 429 responses (e.g., `Retry-After` header or 2^n seconds)
2. Track 429s separately from 4xx/5xx errors
3. Consider increasing `TokenBucketRateLimiter` rate or reducing scan frequency

---

### Issue #3 — HIGH: `FleaMarketDiscovery._seen` Set Never Cleared

**Severity:** HIGH
**Impact:** Unbounded memory growth over long-running sessions

**Location:** `infra/flea_market_discovery.py:63`

```python
self._seen: set[str] = set()
```

The `_seen` set is populated in `_parse_v2_pair_created()` (line 191) but never cleared. Over days/weeks of operation, this set will grow without bound as every discovered pair key is retained.

**Fix:** Add TTL-based eviction or periodic clearing (e.g., clear every N scan cycles or when set exceeds a size threshold).

---

### Issue #4 — HIGH: `SourceFetcher` Creates New `httpx.AsyncClient` Per Request

**Severity:** HIGH
**Impact:** Connection leak, TLS handshake overhead, potential fd exhaustion

**Location:** `infra/source_fetcher.py:36`

```python
async with httpx.AsyncClient(timeout=30.0) as client:
    response = await client.get(ETHERSCAN_V2_BASE_URL, params=params)
```

Each call to `fetch_source()` creates and destroys an HTTP client. This means:
- A new TCP+TLS handshake for every Etherscan request
- No connection reuse
- Potential file descriptor exhaustion under load

**Fix:** Create a single `httpx.AsyncClient` instance in `SourceFetcher.__init__()` and reuse it, or accept an injected client.

---

### Issue #5 — HIGH: Missing `ETHERSCAN_API_KEY` in `.env`

**Severity:** HIGH
**Impact:** `SourceFetcher.fetch_source()` always returns empty string; LLM audit never runs

**Evidence:** `.env.example` does not include `ETHERSCAN_API_KEY`. The `SourceFetcher` defaults to `os.getenv("ETHERSCAN_API_KEY", "")` which returns empty string. Without an API key, the Etherscan V2 API call either fails or returns no source code.

**Downstream Effect:** Even if Issue #1 is fixed and targets are discovered, the source fetch step will fail, preventing any token from being cleared.

**Fix:** Add `ETHERSCAN_API_KEY=<your_key>` to `.env`.

---

### Issue #6 — MEDIUM: LLM Model Mismatch Between Process A and Config

**Severity:** MEDIUM
**Impact:** Process A uses Groq API with `llama-3.3-70b-versatile`, config defines OpenRouter models

**Process A hardcoded model** (`process_a_indexer.py:40-43`):
```python
class LLMSecurityAuditor:
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self.base_url = "https://api.groq.com/openai/v1"
```

**Config models** (`.env.example:5-6`):
```
LLM_MODEL_PRIMARY=deepseek/deepseek-chat-v3-0324:free
LLM_MODEL_FALLBACK=qwen/qwen3-235b-a22b:free
```

Process A ignores the `LLM_MODEL_PRIMARY`/`LLM_MODEL_FALLBACK` env vars and the `LLM_BASE_URL` env var. It always calls Groq with Llama 3.3. The `app.py:90-91` reads the env vars and passes them to `LLMSecurityAuditor`, but the constructor only uses `model` param — it does not accept a `base_url` param.

**Fix:** Update `LLMSecurityAuditor.__init__()` to accept `base_url` parameter, or align Process A with the OpenRouter configuration.

---

### Issue #7 — MEDIUM: Hardcoded Stale WETH Prices

**Severity:** MEDIUM
**Impact:** Liquidity estimation and profit calculation use incorrect WETH prices

| Location | WETH Price | Context |
|----------|------------|---------|
| `infra/flea_market_discovery.py:130` | `$3,800` | Liquidity estimation |
| `process_b_sniper.py:78` | `$3,800` | Scanner default |
| `process_b_sniper.py:206` | `$3,800` | QuantAnalyst default |
| `process_b_sniper.py:262` | `$3,800` | ExecutionGatekeeper default |
| `agents/spread_detector.py:77` | `$2,200` | Pool liquidity calc |

The WETH/USDC pool fetch in `PriceAnomalyScanner._get_weth_price()` does update dynamically (with 60s cache), but the **default fallback** of $3,800 is used when the pool fetch fails or before the first successful fetch. The $2,200 in `spread_detector.py` is even more stale.

**Fix:** Remove hardcoded WETH prices. Use a proper oracle or require at least one successful price fetch before processing.

---

### Issue #8 — MEDIUM: Reserve Pair Ordering Bug in `find_local_spreads()`

**Severity:** MEDIUM
**Impact:** Spread calculation may be incorrect if token ordering in pool differs from expected

**Location:** `infra/local_pricing.py:172-185`

```python
buy_amount_out = compute_v2_output(
    reserve_in=buy_reserves[1],   # Assumes token1 is input
    reserve_out=buy_reserves[0],  # Assumes token0 is output
    amount_in=amount_in,
)
```

This assumes `reserves[1]` is always the input token (WETH) and `reserves[0]` is always the output token. However, the reserve tuple `(r0, r1)` comes from `getReserves()` which returns them in `token0/token1` order. If the exotic token is `token0` and WETH is `token1`, the ordering is reversed.

The scanner in `process_b_sniper.py:132-136` stores reserves by DEX name, not by token position, so the spread calculation may silently produce incorrect results.

**Fix:** Track which reserve index corresponds to WETH vs the exotic token, and use the correct indices in spread computation.

---

### Issue #9 — MEDIUM: `_estimate_liquidity` Incorrect Token Handling

**Severity:** MEDIUM
**Impact:** May return 0 liquidity for valid pools, or incorrect liquidity values

**Location:** `infra/flea_market_discovery.py:110-133`

```python
async def _estimate_liquidity(self, pair_address: str) -> float:
    # ...
    if token0.lower() == WETH_ADDRESS:
        weth_reserve = r0
    elif token0.lower() == USDC_ADDRESS:
        usdc_reserve = r0
        return usdc_reserve / 1e6
    else:
        if token0.lower() in MAJOR_ASSET_BLACKLIST:
            return 0.0
        weth_reserve = r1  # Assumes WETH is token1
    return (weth_reserve / 1e18) * 3800.0
```

When neither token0 is WETH nor USDC, it assumes token1 is WETH (`weth_reserve = r1`). But if token1 is also an exotic token, this returns an incorrect value. Additionally, the `MAJOR_ASSET_BLACKLIST` check on token0 returns 0 for valid WETH/USDC pairs that happen to have a different case.

**Fix:** Explicitly check both token0 and token1 against known quote tokens (WETH, USDC, USDT) and handle all combinations.

---

### Issue #10 — LOW: Async Event Loop `__del__` Exceptions on Startup

**Severity:** LOW (cosmetic)
**Impact:** Noise in logs; no functional impact

**Evidence (log.txt lines 25-74):**
```
Exception ignored in: <function BaseEventLoop.__del__ at 0x7fd0fdfc0040>
ValueError: Invalid file descriptor: -1
```

This is a known Python/asyncio issue when event loops are garbage collected during interpreter shutdown or when multiple loops are created. The `app.py` creates a new event loop in a daemon thread (`start_arbitrage_engine`) while Gradio's own loop runs on the main thread.

**Fix:** Ignore or suppress these warnings in logging config. Not a functional issue.

---

### Issue #11 — LOW: Hardcoded DB Path May Not Exist Locally

**Severity:** LOW
**Impact:** `ClearedTokensDB` fails if `/data/` directory doesn't exist

**Location:** `db/cleared_tokens.py:16`
```python
DB_PATH = Path("/data/cleared_tokens.db")
```

The `/data/` path is specific to Hugging Face Spaces persistent storage. When running locally (e.g., via `orchestrator.py`), this directory may not exist. The `_init_db()` method does call `self.db_path.parent.mkdir(parents=True, exist_ok=True)`, so it will create the directory, but this may fail on systems without write access to `/`.

**Fix:** Make `DB_PATH` configurable via env var, defaulting to a local path like `./cleared_tokens.db`.

---

### Issue #12 — LOW: Import Inside Loop

**Severity:** LOW (performance)
**Impact:** Minor overhead from repeated imports

**Location:** `process_a_indexer.py:188`
```python
for dex_name in ["uniswap_v2", "sushiswap", "camelot_v2"]:
    pair = compute_v2_pair_address(...)  # This import is inside _process_target
```

The `from infra.create2 import compute_v2_pair_address` at line 188 is inside `_process_target()`, which is called per-target. Python caches imports, so this is not a bug, but it's unconventional.

**Fix:** Move the import to the top of the file.

---

### Issue #13 — LOW: Duplicate `AuditResult` Classes

**Severity:** LOW (maintainability)
**Impact:** Confusion about which `AuditResult` to use

**Locations:**
- `process_a_indexer.py:34` — `@dataclass AuditResult`
- `agents/state.py:41` — Pydantic `AuditResult`

Both have the same structure (`is_safe: bool`, `threats: list[str]`) but different base classes. The runtime Process A uses the dataclass version; the LangGraph pipeline uses the Pydantic version.

**Fix:** Consolidate to a single `AuditResult` (prefer Pydantic for consistency with the rest of the state models).

---

### Issue #14 — INFO: LangGraph Pipeline Is Dead Code

**Severity:** INFO
**Impact:** No runtime impact; maintenance burden

**Location:** `graph/pipeline.py` and all `agents/*_node.py` files

The LangGraph `StateGraph` DAG defined in `graph/pipeline.py` is never instantiated by either `orchestrator.py` or `app.py`. The runtime directly uses `ProcessAIndexer` and `ProcessBSniper`. The agent nodes (`filter_gate_node`, `rag_auditor_node`, `slippage_analyst_node`, `execution_node`) are only referenced from `graph/pipeline.py`.

**Recommendation:** Either wire the LangGraph pipeline into the runtime, or remove the dead code to reduce maintenance surface.

---

### Issue #15 — INFO: Inconsistent LLM Client Configuration

**Severity:** INFO
**Impact:** Confusion; different audit quality depending on code path

| Code Path | LLM Provider | Model | Base URL |
|-----------|-------------|-------|----------|
| Process A (`process_a_indexer.py`) | Groq | `llama-3.3-70b-versatile` | `https://api.groq.com/openai/v1` |
| LangGraph (`rag_auditor_node.py`) | Configurable | `LLM_MODEL_PRIMARY` | `LLM_BASE_URL` |

Process A hardcodes Groq + Llama, while the LangGraph path reads from config. Since the runtime uses Process A, the actual LLM is always Groq/Llama regardless of `.env` settings.

---

## 7. Log Analysis Summary

### 7.1 RPC Call Pattern (Per 30s Cycle)

Each Process A scan cycle makes **4 RPC calls**:
1. `eth_blockNumber` — get current block
2. `eth_getLogs` — scan Camelot V2 factory events
3. `eth_getLogs` — scan SushiSwap V2 factory events
4. `eth_getLogs` — scan Uniswap V2 factory events

Over 4.5 hours: ~540 cycles × 4 calls = **~2,160 RPC calls** to `arb1.arbitrum.io/rpc`.

### 7.2 Failover Events

| Time | Event |
|------|-------|
| 14:41:06 | Alchemy 400 → failover toggled to public RPC |
| 15:10:42 | Public RPC 429 → Alchemy 400 → scan failed |
| 15:46:50 | Public RPC 429 → Alchemy 400 → scan failed |

The system correctly fell back to the public RPC but was occasionally rate-limited there as well.

### 7.3 Key Observation

The system **never reached Process B** because Process A never cleared any tokens. The entire 4.5-hour session was spent polling for new pairs and finding zero. This is expected behavior when:
1. The primary RPC is broken (Issue #1)
2. No new V2 pairs were created on the three monitored factories in the 1000-block lookback window
3. The public RPC occasionally rate-limits the `eth_getLogs` calls

---

## 8. Recommendations (Priority Order)

1. **Fix Alchemy API key** — Regenerate and validate before next run
2. **Add `ETHERSCAN_API_KEY`** to `.env` — Required for source fetching
3. **Add RPC health check at startup** — Validate key works before entering main loop
4. **Add retry/backoff for 429 errors** — Exponential backoff with jitter
5. **Clear `_seen` set periodically** — Prevent memory leak
6. **Reuse `httpx.AsyncClient`** in `SourceFetcher` — Connection pooling
7. **Fix WETH price hardcoding** — Use oracle or require successful fetch
8. **Fix reserve ordering in spread calc** — Track token position
9. **Wire LangGraph pipeline or remove dead code** — Reduce maintenance surface
10. **Add integration test with mock RPC** — Verify full discovery→clear→scan→execute flow

---

## 9. File Checksums (for reproducibility)

```
app.py              — 220 lines
orchestrator.py     — 184 lines
process_a_indexer.py — 233 lines
process_b_sniper.py — 389 lines
agents/state.py     — 73 lines
agents/minifier.py  — 55 lines
infra/rpc_manager.py — 99 lines
infra/flea_market_discovery.py — 275 lines
infra/source_fetcher.py — 59 lines
infra/local_pricing.py — 223 lines
infra/pool_fetcher.py — 125 lines
infra/create2.py    — 160 lines
infra/rate_limiter.py — 28 lines
infra/live_executor.py — 268 lines
db/cleared_tokens.py — 177 lines
db/cache.py         — 155 lines
config/factories.py — 73 lines
config/constants.py — 73 lines
monitoring/alerts.py — 117 lines
graph/pipeline.py   — 110 lines
agents/rag_auditor_node.py — 207 lines
agents/filter_gate_node.py — 56 lines
agents/slippage_analyst_node.py — 50 lines
agents/execution_node.py — 144 lines
```
