# Long-Tail Arbitrage System — Build Plan

> **Target:** 24/7 autonomous operation on a bare Linux VPS  
> **Network:** Arbitrum One (L2)  
> **Orchestrator:** LangGraph  
> **Blockchain SDK:** web3.py (async)  
> **LLM Provider:** Google Gemini API  

---

## Phase 1: Dependency Mapping & Environment Configuration

### 1.1 Project Directory Structure

```
longtail/
├── AGENTS.md                    # Agent architecture contract (existing)
├── build_plan.md                # This file
├── pyproject.toml               # Project metadata, dependencies, tool config
├── .env.example                 # Template for environment variables
├── .env                         # Live secrets (gitignored, chmod 0600)
├── config/
│   ├── settings.py              # Pydantic BaseSettings (dev/prod profiles)
│   └── constants.py             # Hardcoded addresses, ABI fragments, thresholds
├── db/
│   ├── schema.sql               # SQLite table definitions
│   ├── cache.py                 # aiosqlite cache layer (contract source, audit results)
│   └── blacklist.py             # Blacklist CRUD operations
├── infra/
│   ├── rate_limiter.py          # Token-bucket async rate limiter
│   ├── rpc_manager.py           # RPC provider failover + multicall aggregation
│   ├── multicall.py             # Batched JSON-RPC read aggregation
│   └── keystore.py              # Encrypted private key loading + signing
├── agents/
│   ├── state.py                 # ArbitrageState Pydantic model + Status enum
│   ├── ingestion_node.py        # Payload initialization from upstream hook
│   ├── filter_gate_node.py      # Deterministic firewall (Agent A)
│   ├── rag_auditor_node.py      # Minification + Gemini audit (Agent B)
│   ├── slippage_analyst_node.py # AMM math validation (Agent C)
│   ├── execution_node.py        # Dual-mode settlement engine (Agent D)
│   └── minifier.py              # Regex-driven Solidity source minifier
├── graph/
│   ├── pipeline.py              # LangGraph StateGraph compilation + edge routing
│   └── checkpoint.py            # SQLite-backed LangGraph checkpointer
├── monitoring/
│   ├── logger.py                # Structured JSON logger configuration
│   ├── metrics.py               # Prometheus metric collectors
│   └── alerts.py                # Telegram alert dispatcher
├── tests/
│   ├── conftest.py              # Shared fixtures, mock state factories
│   ├── test_filter_gate.py      # Agent A unit tests
│   ├── test_rag_auditor.py      # Agent B unit tests (mocked Gemini)
│   ├── test_slippage.py         # Agent C unit tests
│   ├── test_execution.py        # Agent D unit tests (dry_run mode)
│   ├── test_minifier.py         # Minifier compression tests
│   ├── test_rate_limiter.py     # Rate limiter unit tests
│   ├── test_cache.py            # Cache hit/miss/TTL tests
│   └── test_pipeline.py         # Full pipeline integration tests
├── scripts/
│   ├── seed_blacklist.py        # Import blacklist JSON into SQLite
│   ├── run_dry.py               # CLI entrypoint for dry-run testing
│   └── run_live.py              # CLI entrypoint for live operation
└── systemd/
    └── longtail.service         # systemd unit file for 24/7 operation
```

### 1.2 Python Dependencies

```toml
# pyproject.toml [project.dependencies]
[project]
name = "longtail-arbitrage"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "langgraph>=0.2.0",
    "langchain-core>=0.3.0",
    "web3[async]>=7.0.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.5.0",
    "aiosqlite>=0.20.0",
    "google-genai>=1.0.0",
    "eth-account>=0.13.0",
    "httpx>=0.27.0",
    "structlog>=24.4.0",
    "prometheus-client>=0.21.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.7.0",
    "mypy>=1.12.0",
    "respx>=0.21.0",
]
```

### 1.3 Environment Variables & Config Schema

```python
# config/settings.py
from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    # Execution mode
    dry_run: bool = Field(default=True, description="True = simulate, False = sign and broadcast")

    # RPC endpoints
    alchemy_api_key: str
    alchemy_rpc_url: str = "https://arb-mainnet.g.alchemy.com/v2/{alchemy_api_key}"
    fallback_rpc_url: str = "https://arb1.arbitrum.io/rpc"
    flashbots_rpc_url: str = "https://rpc.flashbots.net/fast"

    # Gemini API
    gemini_api_key: str
    gemini_model_primary: str = "gemini-2.0-flash"
    gemini_model_fallback: str = "gemini-1.5-flash"
    gemini_max_retries: int = 3
    gemini_temperature: float = 0.0

    # Keystore
    keystore_path: str = "./keystore.json"
    keystore_passphrase: str

    # Thresholds
    min_liquidity_usd: float = 2500.0
    min_net_profit_usd: float = 0.50
    gas_baseline_usd: float = 0.02
    max_trade_size_usd: float = 500.0

    # Infrastructure
    max_concurrent_graphs: int = 5
    rpc_rate_limit_per_sec: int = 10
    cache_ttl_hours: int = 24
    db_path: str = "./longtail.db"

    # Monitoring
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    prometheus_port: int = 9090
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
```

**`.env.example`:**
```bash
DRY_RUN=true
ALCHEMY_API_KEY=your_alchemy_key_here
GEMINI_API_KEY=your_gemini_key_here
KEYSTORE_PATH=./keystore.json
KEYSTORE_PASSPHRASE=your_passphrase_here
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
LOG_LEVEL=INFO
```

### 1.4 SQLite Database Schema

```sql
-- db/schema.sql

CREATE TABLE IF NOT EXISTS contract_cache (
    token_address TEXT PRIMARY KEY,
    is_verified   INTEGER NOT NULL,
    raw_source    TEXT,
    minified_source TEXT,
    fetched_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_cache (
    token_address TEXT PRIMARY KEY,
    is_safe       INTEGER NOT NULL,
    threats       TEXT,
    audited_at    REAL NOT NULL,
    FOREIGN KEY (token_address) REFERENCES contract_cache(token_address)
);

CREATE TABLE IF NOT EXISTS blacklist (
    token_address TEXT PRIMARY KEY,
    reason        TEXT,
    added_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_log (
    run_id        TEXT PRIMARY KEY,
    token_address TEXT NOT NULL,
    pool_address  TEXT NOT NULL,
    status        TEXT NOT NULL,
    net_profit_usd REAL,
    tx_hash       TEXT,
    reason        TEXT,
    dry_run       INTEGER NOT NULL,
    created_at    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cache_ttl ON contract_cache(fetched_at);
CREATE INDEX IF NOT EXISTS idx_audit_ttl ON audit_cache(audited_at);
CREATE INDEX IF NOT EXISTS idx_exec_status ON execution_log(status);
```

### 1.5 systemd Service Configuration

```ini
# systemd/longtail.service
[Unit]
Description=Long-Tail Arbitrage Engine
After=network.target

[Service]
Type=simple
User=longtail
WorkingDirectory=/opt/longtail
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/longtail/venv/bin/python -m scripts.run_live
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=longtail

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/longtail

[Install]
WantedBy=multi-user.target
```

**Deployment commands:**
```bash
# Initial setup
sudo useradd -r -s /bin/false longtail
sudo mkdir -p /opt/longtail
sudo chown longtail:longtail /opt/longtail
python3.12 -m venv /opt/longtail/venv
/opt/longtail/venv/bin/pip install -e ".[dev]"

# Service installation
sudo cp systemd/longtail.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable longtail
sudo systemctl start longtail

# Operations
sudo systemctl status longtail
sudo journalctl -u longtail -f
sudo systemctl restart longtail
```

---

## Phase 2: LangGraph State & Node Pipeline Implementation

### 2.0 Shared State Schema

```python
# agents/state.py
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum

class Status(str, Enum):
    PENDING    = "PENDING"
    FILTERED   = "FILTERED"
    AUDITED    = "AUDITED"
    VALIDATED  = "VALIDATED"
    AUTHORIZED = "AUTHORIZED"
    ABORTED    = "ABORTED"
    EXECUTED   = "EXECUTED"

class ArbitrageState(BaseModel):
    # Identity
    run_id: str
    token_address: str
    pool_address: str

    # Ingested data
    liq_usd: float
    is_verified: bool
    gross_spread_pct: float
    trade_size_usd: float
    pool_reserve_usd: float
    gas_usd: float = Field(default=0.02)

    # Contract audit
    minified_source: Optional[str] = None
    audit_is_safe: Optional[bool] = None
    audit_threats: Optional[List[str]] = None

    # Quant validation
    expected_slippage_pct: Optional[float] = None
    net_profit_usd: Optional[float] = None

    # Execution
    tx_hash: Optional[str] = None
    dry_run: bool = True
    simulated_receipt: Optional[dict] = None

    # Control
    status: Status = Status.PENDING
    reason: Optional[str] = None
```

### 2.1 `ingestion_node` — Payload Initialization

**Purpose:** Accept a structured payload dict from the upstream hook and initialize `ArbitrageState`.

**Implementation steps:**

1. Define the upstream payload contract:
   ```python
   class IngestionPayload(BaseModel):
       run_id: str
       token_address: str
       pool_address: str
       liq_usd: float
       is_verified: bool
       gross_spread_pct: float
       trade_size_usd: float
       pool_reserve_usd: float
   ```

2. The node function receives the payload, validates it via Pydantic, and constructs `ArbitrageState` with `status = PENDING`.

3. Attach the global `dry_run` flag from `Settings` into the state.

4. Log the ingestion event with `run_id`, `token_address`, and `liq_usd`.

5. Return the initialized state. No abort conditions at this node — validation happens downstream.

**Edge routing:** Always routes to `filter_gate_node`.

---

### 2.2 `filter_gate_node` — Deterministic Firewall (Agent A)

**Purpose:** Zero-cost programmatic screening. No LLM calls.

**Implementation steps:**

1. **Verification check:**
   ```python
   if not state.is_verified:
       state.status = Status.ABORTED
       state.reason = "unverified contract bytecode"
       return state
   ```

2. **Liquidity check:**
   ```python
   if state.liq_usd < settings.min_liquidity_usd:
       state.status = Status.ABORTED
       state.reason = f"liquidity ${state.liq_usd:.2f} below ${settings.min_liquidity_usd:.0f} floor"
       return state
   ```

3. **Blacklist check:**
   ```python
   is_blacklisted = await blacklist_db.contains(state.token_address)
   if is_blacklisted:
       state.status = Status.ABORTED
       state.reason = "blacklisted token address"
       return state
   ```

4. **Trade size cap:**
   ```python
   if state.trade_size_usd > settings.max_trade_size_usd:
       state.status = Status.ABORTED
       state.reason = f"trade size ${state.trade_size_usd:.2f} exceeds ${settings.max_trade_size_usd:.0f} cap"
       return state
   ```

5. If all checks pass: `state.status = Status.FILTERED`.

**Edge routing:**
- `status == ABORTED` → route to `terminal_node` (log + cleanup)
- `status == FILTERED` → route to `rag_auditor_node`

---

### 2.3 `rag_auditor_node` — Minification + Gemini Audit (Agent B)

**Purpose:** Fetch contract source, minify it, and run LLM-based security audit.

**Implementation steps:**

1. **Cache lookup:**
   ```python
   cached = await cache_db.get_audit(state.token_address, ttl_hours=settings.cache_ttl_hours)
   if cached is not None:
       state.audit_is_safe = cached.is_safe
       state.audit_threats = cached.threats
       state.minified_source = cached.minified_source
       if not cached.is_safe:
           state.status = Status.ABORTED
           state.reason = f"audit failed (cached): {cached.threats}"
       else:
           state.status = Status.AUDITED
       return state
   ```

2. **Fetch source via Etherscan MCP / Alchemy:**
   ```python
   raw_source = await rpc_manager.get_contract_source(state.token_address)
   ```

3. **Minify:**
   ```python
   from agents.minifier import minify_solidity
   state.minified_source = minify_solidity(raw_source)
   ```

4. **Call Gemini API with structured output enforcement:**
   ```python
   from google import genai
   client = genai.Client(api_key=settings.gemini_api_key)

   AUDIT_SYSTEM_PROMPT = """You are a Solidity security auditor. You will receive minified smart contract source code.

Your task: inspect the code for the following vulnerability classes:
1. Hidden transfer taxes (fees deducted on every transfer that are not disclosed)
2. Malicious mint mechanisms (unrestricted or owner-only minting that dilutes holders)
3. Freeze or blacklist parameters (functions that can lock user funds or block transfers)
4. Balance modification vulnerabilities (direct balance manipulation outside of standard transfer logic)
5. Honeypot patterns (buy allowed, sell blocked or heavily penalized)

You must respond with a single valid JSON object matching this schema:
{
  "is_safe": boolean,
  "threats": [string]
}

- "is_safe" is true only if NONE of the above vulnerability classes are detected.
- "threats" is a list of short strings describing each detected vulnerability. Empty list if none.

RULES:
- Output ONLY the JSON object. No markdown, no explanation, no prose, no code fences.
- If the code is too short or empty to audit, set is_safe=false and threats=["insufficient source code"]."""

   response = await client.aio.models.generate_content(
       model=settings.gemini_model_primary,
       contents=state.minified_source,
       config=types.GenerateContentConfig(
           system_instruction=AUDIT_SYSTEM_PROMPT,
           temperature=0.0,
           response_mime_type="application/json",
       )
   )
   ```

5. **Parse and validate response:**
   ```python
   result = AuditResult.model_validate_json(response.text)
   ```
   - On JSON parse failure: attempt with fallback model. On second failure: `ABORTED("audit LLM parse failure")`.

6. **State mutation:**
   ```python
   state.audit_is_safe = result.is_safe
   state.audit_threats = result.threats
   if not result.is_safe:
       state.status = Status.ABORTED
       state.reason = f"audit failed: {result.threats}"
   else:
       state.status = Status.AUDITED
   ```

7. **Write to cache:**
   ```python
   await cache_db.store_audit(state.token_address, result, state.minified_source)
   ```

**Edge routing:**
- `status == ABORTED` → `terminal_node`
- `status == AUDITED` → `slippage_analyst_node`

---

### 2.4 `slippage_analyst_node` — AMM Math Validation (Agent C)

**Purpose:** Pure local computation. No LLM, no RPC.

**Implementation steps:**

1. **Calculate expected slippage:**
   ```python
   expected_slippage_pct = (state.trade_size_usd / state.pool_reserve_usd) * 100
   ```

2. **Calculate net profit:**
   ```python
   net_profit_usd = (
       ((state.gross_spread_pct - expected_slippage_pct) / 100)
       * state.trade_size_usd
       - state.gas_usd
   )
   ```

3. **Store computed values:**
   ```python
   state.expected_slippage_pct = round(expected_slippage_pct, 6)
   state.net_profit_usd = round(net_profit_usd, 4)
   ```

4. **Validate against execution floor:**
   ```python
   if state.net_profit_usd < settings.min_net_profit_usd:
       state.status = Status.ABORTED
       state.reason = f"net profit ${state.net_profit_usd:.4f} below ${settings.min_net_profit_usd:.2f} floor"
       return state
   ```

5. If profitable: `state.status = Status.VALIDATED`.

**Edge routing:**
- `status == ABORTED` → `terminal_node`
- `status == VALIDATED` → `execution_node`

---

### 2.5 `execution_node` — Dual-Mode Settlement Engine (Agent D)

**Purpose:** Final gate checks + conditional transaction execution.

**Implementation steps:**

1. **Pre-flight validation:**
   ```python
   if state.status != Status.VALIDATED:
       state.status = Status.ABORTED
       state.reason = "invalid upstream state"
       return state
   if state.audit_is_safe is not True:
       state.status = Status.ABORTED
       state.reason = "audit not passed"
       return state
   if state.net_profit_usd < settings.min_net_profit_usd:
       state.status = Status.ABORTED
       state.reason = "profit below execution floor"
       return state
   ```

2. **Construct transaction payload:**
   ```python
   tx_payload = {
       "to": ROUTER_ADDRESS,
       "data": encode_swap_calldata(state.token_address, state.pool_address, state.trade_size_usd),
       "value": 0,
       "gas": estimate_gas(state),
       "gasPrice": await w3.eth.gas_price,
       "nonce": await w3.eth.get_transaction_count(wallet_address),
       "chainId": 42161,  # Arbitrum One
   }
   ```

3. **Branch on `dry_run` flag:**

   **Dry Run Mode (`state.dry_run == True`):**
   ```python
   state.simulated_receipt = {
       "payload": tx_payload,
       "expected_gas_usd": state.gas_usd,
       "expected_net_profit_usd": state.net_profit_usd,
       "expected_slippage_pct": state.expected_slippage_pct,
       "token_address": state.token_address,
       "pool_address": state.pool_address,
       "trade_size_usd": state.trade_size_usd,
       "mode": "DRY_RUN",
   }
   state.status = Status.EXECUTED
   state.tx_hash = "0x" + "0" * 64  # Placeholder

   logger.info(
       "[DRY RUN TEST SUITE OUTCOME]",
       run_id=state.run_id,
       token=state.token_address,
       net_profit=state.net_profit_usd,
       slippage=state.expected_slippage_pct,
   )
   ```

   **Live Mode (`state.dry_run == False`):**
   ```python
   # Sign locally
   signed_tx = keystore.sign_transaction(tx_payload)

   # Submit via Flashbots Protect (MEV protection)
   tx_hash = await rpc_manager.send_private_transaction(signed_tx)

   if tx_hash:
       state.tx_hash = tx_hash
       state.status = Status.EXECUTED
       await alerts.send_telegram(
           f"EXECUTED: {state.token_address}\n"
           f"Profit: ${state.net_profit_usd:.4f}\n"
           f"TX: {tx_hash}"
       )
   else:
       state.status = Status.ABORTED
       state.reason = "tx submission failed"
   ```

4. **Persist execution log:**
   ```python
   await db.insert_execution_log(state)
   ```

**Edge routing:** Always routes to `terminal_node`.

---

### 2.6 Infrastructure Layer

#### 2.6.1 Token-Bucket Rate Limiter (`infra/rate_limiter.py`)

```python
import asyncio
import time

class TokenBucketRateLimiter:
    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1
```

#### 2.6.2 RPC Manager with Failover (`infra/rpc_manager.py`)

```python
class RPCManager:
    def __init__(self, settings: Settings, rate_limiter: TokenBucketRateLimiter):
        self.primary_url = settings.alchemy_rpc_url
        self.fallback_url = settings.fallback_rpc_url
        self.flashbots_url = settings.flashbots_rpc_url
        self.rate_limiter = rate_limiter
        self._failover_active = False
        self._consecutive_failures = 0

    async def _call(self, method: str, params: list) -> dict:
        await self.rate_limiter.acquire()
        url = self.fallback_url if self._failover_active else self.primary_url
        try:
            response = await self._http_post(url, method, params)
            self._consecutive_failures = 0
            return response
        except (httpx.HTTPStatusError, httpx.ConnectError):
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._failover_active = not self._failover_active
                self._consecutive_failures = 0
                logger.warning("RPC failover triggered", active=self._failover_active)
            raise

    async def send_private_transaction(self, signed_tx) -> str | None:
        raw_hex = signed_tx.raw_transaction.hex()
        try:
            result = await self._http_post(
                self.flashbots_url,
                "eth_sendRawTransaction",
                [raw_hex]
            )
            return result.get("result")
        except Exception as e:
            logger.error("Flashbots submission failed", error=str(e))
            return None
```

#### 2.6.3 Multicall Aggregation (`infra/multicall.py`)

```python
class MulticallBatcher:
    MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

    async def aggregate(self, calls: list[dict]) -> list[bytes]:
        encoded = encode_multicall3_aggregate(calls)
        result = await rpc_manager.call_contract(
            self.MULTICALL3_ADDRESS, encoded
        )
        return decode_multicall3_results(result)
```

**Usage pattern per graph instance:**
```python
results = await multicall.aggregate([
    {"target": WETH, "callData": encode_balance_of(pool_address)},
    {"target": token_address, "callData": encode_decimals()},
    {"target": pool_address, "callData": encode_get_reserves()},
])
```

#### 2.6.4 Contract Source Cache (`db/cache.py`)

```python
class ContractCache:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def get_source(self, token_address: str, ttl_hours: int = 24) -> Optional[dict]:
        cutoff = time.time() - (ttl_hours * 3600)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT is_verified, raw_source, minified_source FROM contract_cache "
                "WHERE token_address = ? AND fetched_at > ?",
                (token_address, cutoff)
            )
            row = await cursor.fetchone()
            if row:
                return {"is_verified": bool(row[0]), "raw_source": row[1], "minified_source": row[2]}
        return None

    async def store_source(self, token_address: str, is_verified: bool, raw: str, minified: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO contract_cache VALUES (?, ?, ?, ?, ?)",
                (token_address, int(is_verified), raw, minified, time.time())
            )
            await db.commit()

    async def get_audit(self, token_address: str, ttl_hours: int = 24) -> Optional[AuditResult]:
        cutoff = time.time() - (ttl_hours * 3600)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT is_safe, threats FROM audit_cache "
                "WHERE token_address = ? AND audited_at > ?",
                (token_address, cutoff)
            )
            row = await cursor.fetchone()
            if row:
                return AuditResult(is_safe=bool(row[0]), threats=json.loads(row[1]))
        return None
```

#### 2.6.5 Solidity Minifier (`agents/minifier.py`)

```python
import re

def minify_solidity(source: str) -> str:
    original_size = len(source)

    code = re.sub(r'//[^\n]*', '', source)
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
    code = re.sub(r'pragma\s+solidity\s+[^;]+;', '', code)
    code = re.sub(r'\b(interface|abstract)\b', '', code)
    code = re.sub(r'\s+', ' ', code).strip()

    compressed_size = len(code)
    ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0

    if ratio < 60:
        logger.warning(
            "minifier compression below 60%",
            original=original_size,
            compressed=compressed_size,
            ratio=f"{ratio:.1f}%"
        )

    return code
```

---

### 2.7 Graph Compilation & Edge Routing

```python
# graph/pipeline.py
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

def build_pipeline(settings: Settings) -> StateGraph:
    graph = StateGraph(ArbitrageState)

    graph.add_node("ingestion",         ingestion_node)
    graph.add_node("filter_gate",       filter_gate_node)
    graph.add_node("rag_auditor",       rag_auditor_node)
    graph.add_node("slippage_analyst",  slippage_analyst_node)
    graph.add_node("execution",         execution_node)
    graph.add_node("terminal",          terminal_node)

    graph.set_entry_point("ingestion")

    graph.add_edge("ingestion", "filter_gate")

    graph.add_conditional_edges(
        "filter_gate",
        route_on_status,
        {"abort": "terminal", "continue": "rag_auditor"}
    )

    graph.add_conditional_edges(
        "rag_auditor",
        route_on_status,
        {"abort": "terminal", "continue": "slippage_analyst"}
    )

    graph.add_conditional_edges(
        "slippage_analyst",
        route_on_status,
        {"abort": "terminal", "continue": "execution"}
    )

    graph.add_edge("execution", "terminal")
    graph.add_edge("terminal", END)

    return graph

def route_on_status(state: ArbitrageState) -> str:
    return "abort" if state.status == Status.ABORTED else "continue"

async def compile_graph(settings: Settings):
    checkpointer = AsyncSqliteSaver.from_conn_string(settings.db_path)
    graph = build_pipeline(settings)
    return graph.compile(checkpointer=checkpointer)
```

---

## Phase 3: Testing, Verification & Integration

### 3.1 Test Matrix — 4 Validation Profiles

Each profile exercises a specific failure or success path through the full pipeline in `dry_run` mode.

#### Profile Alpha: Happy Path (All Checks Pass)

```python
# tests/test_pipeline.py
ALPHA_PAYLOAD = {
    "run_id": "alpha-001",
    "token_address": "0xAAA...AAA",
    "pool_address": "0xBBB...BBB",
    "liq_usd": 50000.0,
    "is_verified": True,
    "gross_spread_pct": 8.5,
    "trade_size_usd": 200.0,
    "pool_reserve_usd": 50000.0,
}
# Expected: PENDING -> FILTERED -> AUDITED -> VALIDATED -> EXECUTED (dry_run)
# Expected net_profit = ((8.5 - 0.4) / 100) * 200 - 0.02 = $16.18
```

#### Profile Beta: Blacklisted Token

```python
BETA_PAYLOAD = {
    "run_id": "beta-001",
    "token_address": "0xDEAD...DEAD",  # Pre-seeded in blacklist
    "pool_address": "0xBBB...BBB",
    "liq_usd": 50000.0,
    "is_verified": True,
    "gross_spread_pct": 12.0,
    "trade_size_usd": 200.0,
    "pool_reserve_usd": 50000.0,
}
# Expected: PENDING -> FILTERED -> ABORTED("blacklisted token address")
# Gemini API must NOT be called.
```

#### Profile Gamma: Honeypot Exploit Detection

```python
GAMMA_PAYLOAD = {
    "run_id": "gamma-001",
    "token_address": "0xHONE...YPOT",
    "pool_address": "0xCCC...CCC",
    "liq_usd": 10000.0,
    "is_verified": True,
    "gross_spread_pct": 15.0,
    "trade_size_usd": 100.0,
    "pool_reserve_usd": 10000.0,
}
# Mock Gemini returns: {"is_safe": false, "threats": ["honeypot: sell blocked", "hidden 99% transfer tax"]}
# Expected: PENDING -> FILTERED -> AUDITED (unsafe) -> ABORTED("audit failed: [...]")
# Slippage node must NOT be reached.
```

#### Profile Delta: Slippage Margin Decay

```python
DELTA_PAYLOAD = {
    "run_id": "delta-001",
    "token_address": "0xDDD...DDD",
    "pool_address": "0xEEE...EEE",
    "liq_usd": 3000.0,
    "is_verified": True,
    "gross_spread_pct": 3.0,
    "trade_size_usd": 500.0,
    "pool_reserve_usd": 3000.0,
}
# Expected slippage = (500 / 3000) * 100 = 16.67%
# Net profit = ((3.0 - 16.67) / 100) * 500 - 0.02 = -$68.37
# Expected: PENDING -> FILTERED -> AUDITED -> ABORTED("net profit $-68.37 below $0.50 floor")
```

### 3.2 Dry Run Verification Protocol

```python
# scripts/run_dry.py
import asyncio
from config.settings import Settings
from graph.pipeline import compile_graph

async def main():
    settings = Settings(dry_run=True)
    app = await compile_graph(settings)

    test_payloads = [ALPHA_PAYLOAD, BETA_PAYLOAD, GAMMA_PAYLOAD, DELTA_PAYLOAD]

    for payload in test_payloads:
        config = {"configurable": {"thread_id": payload["run_id"]}}
        result = await app.ainvoke(payload, config=config)

        print(f"\n{'='*60}")
        print(f"[DRY RUN TEST SUITE OUTCOME] run_id={result['run_id']}")
        print(f"  Status:      {result['status']}")
        print(f"  Reason:      {result.get('reason', 'N/A')}")
        print(f"  Net Profit:  ${result.get('net_profit_usd', 'N/A')}")
        print(f"  TX Hash:     {result.get('tx_hash', 'N/A')}")
        print(f"{'='*60}")

asyncio.run(main())
```

**Run command:**
```bash
python -m scripts.run_dry
```

### 3.3 Anvil Fork Testing Setup

```bash
# Start a local Arbitrum One fork for integration testing
anvil --fork-url https://arb-mainnet.g.alchemy.com/v2/$ALCHEMY_API_KEY \
      --fork-block-number latest \
      --port 8545

# Point tests at the local fork
ANVIL_RPC_URL=http://localhost:8545 pytest tests/test_execution.py -v
```

### 3.4 CI/CD Pipeline

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Lint
        run: ruff check .

      - name: Type check
        run: mypy agents/ infra/ graph/ config/

      - name: Test
        run: pytest tests/ -v --cov=agents --cov=infra --cov-report=term-missing
        env:
          DRY_RUN: "true"
          ALCHEMY_API_KEY: "test"
          GEMINI_API_KEY: "test"
          KEYSTORE_PASSPHRASE: "test"
```

### 3.5 24/7 VPS Deployment Checklist

```bash
# 1. System preparation
sudo apt update && sudo apt install -y python3.12 python3.12-venv sqlite3

# 2. Create service user
sudo useradd -r -s /bin/false longtail

# 3. Deploy application
sudo mkdir -p /opt/longtail
sudo chown $USER:$USER /opt/longtail
# Copy project files to /opt/longtail
python3.12 -m venv /opt/longtail/venv
/opt/longtail/venv/bin/pip install -e .

# 4. Configure secrets
cp .env.example /opt/longtail/.env
chmod 0600 /opt/longtail/.env
# Edit .env with production values

# 5. Initialize database
/opt/longtail/venv/bin/python -m scripts.seed_blacklist

# 6. Install systemd service
sudo cp systemd/longtail.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now longtail

# 7. Verify
sudo systemctl status longtail
sudo journalctl -u longtail -f

# 8. Log rotation
sudo tee /etc/logrotate.d/longtail << 'EOF'
/var/log/longtail/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    postrotate
        systemctl reload longtail || true
    endscript
}
EOF
```

---

## Phase 4: Operational Runbook

### 4.1 Monitoring & Alerting

| Metric | Source | Alert Threshold |
|--------|--------|----------------|
| Graph instances running | Prometheus gauge | < 1 for > 5 min |
| Gemini API latency | Prometheus histogram | p99 > 10s |
| RPC 429 errors | Prometheus counter | > 10 in 1 min |
| Abort rate | execution_log table | > 80% in 1 hour |
| Successful executions | execution_log table | Alert on each (Telegram) |
| Cache hit rate | Prometheus gauge | < 20% (indicates cache issues) |

### 4.2 Common Issues & Resolution

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| HTTP 429 flood | Rate limiter misconfigured | Lower `rpc_rate_limit_per_sec` in `.env` |
| Gemini parse failures | Model returning non-JSON | Check `gemini_model_primary`, try fallback model |
| High abort rate | Upstream data quality degraded | Inspect `execution_log` table, review upstream hook |
| OOM kills | Too many concurrent graphs | Lower `max_concurrent_graphs` in `.env` |
| Stale cache | TTL too long | Lower `cache_ttl_hours` in `.env` |

### 4.3 Maintenance Commands

```bash
# View recent executions
sqlite3 /opt/longtail/longtail.db "SELECT run_id, status, net_profit_usd, created_at FROM execution_log ORDER BY created_at DESC LIMIT 20;"

# Clear expired cache entries
sqlite3 /opt/longtail/longtail.db "DELETE FROM contract_cache WHERE fetched_at < $(date -d '24 hours ago' +%s);"
sqlite3 /opt/longtail/longtail.db "DELETE FROM audit_cache WHERE audited_at < $(date -d '24 hours ago' +%s);"

# Add token to blacklist
sqlite3 /opt/longtail/longtail.db "INSERT OR IGNORE INTO blacklist VALUES ('0x...', 'manual flag', $(date +%s));"

# Restart after config change
sudo systemctl restart longtail

# View live logs
sudo journalctl -u longtail -f --no-pager
```
