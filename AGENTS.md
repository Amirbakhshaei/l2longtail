# Long-Tail Arbitrage System — Agent Architecture Contract

> **Network:** Arbitrum One (L2)  
> **Execution Model:** Asynchronous non-blocking DAG with shared typed state  
> **Token Policy:** Zero conversational overhead — all agent I/O is strict JSON

---

## 1. System Architecture & Topology

### 1.1 Directed Acyclic Graph (DAG) Flow

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────────┐     ┌──────────────────────┐
│  INGESTION  │────▶│  A. FILTER GATE      │────▶│  B. SMART CONTRACT  │────▶│  C. SLIPPAGE & QUANT │
│  (upstream) │     │  (Programmatic       │     │     AUDITOR         │     │     ANALYST          │
│             │     │   Firewall)          │     │  (Scam Detection)   │     │  (Math Validator)    │
└─────────────┘     └──────────────────────┘     └─────────────────────┘     └──────────────────────┘
                                                                       │
                                                                       ▼
                                                            ┌──────────────────────┐
                                                            │  D. EXECUTION        │
                                                            │     GATEKEEPER       │
                                                            │  (Settlement Auth)   │
                                                            └──────────────────────┘
```

**Routing Rules:**
- Any node may mutate `state.status = "ABORTED"` with a `reason` string — this short-circuits all downstream nodes.
- No node may skip or reorder. Each node reads the mutated state from its predecessor.

### 1.2 Shared State Schema

```python
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum

class Status(str, Enum):
    PENDING = "PENDING"
    FILTERED = "FILTERED"
    AUDITED = "AUDITED"
    VALIDATED = "VALIDATED"
    AUTHORIZED = "AUTHORIZED"
    ABORTED = "ABORTED"
    EXECUTED = "EXECUTED"

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
    gas_usd: float = Field(default=0.02, description="L2 Arbitrum baseline gas overhead")

    # Contract audit
    minified_source: Optional[str] = None
    audit_is_safe: Optional[bool] = None
    audit_threats: Optional[List[str]] = None

    # Quant validation
    expected_slippage_pct: Optional[float] = None
    net_profit_usd: Optional[float] = None

    # Execution
    tx_hash: Optional[str] = None

    # Control
    status: Status = Status.PENDING
    reason: Optional[str] = None
```

### 1.3 System-Wide Execution Constraints

- **Async non-blocking:** All nodes run as independent coroutines; no node blocks the event loop.
- **L2 low-latency path:** All RPC calls target Arbitrum One private endpoints. Gas baseline is hardcoded at `$0.02`.
- **Zero conversational overhead:** Every LLM call must include a system prompt that forbids prose. Responses are parsed as JSON; any parse failure triggers immediate `ABORTED`.
- **Deterministic-first:** All numerical checks (liquidity, verification, blacklist, slippage, profit floor) are executed in local code **before** any LLM invocation.

---

## 2. Token-Efficiency & Pre-Filtering Protocol

### 2.1 Token Economy Rules

1. **Never send raw contract source to an LLM.** Always run the [Code Minifier](Skills/code_minifier/skill.md) first.
2. **Never invoke an LLM for numerical comparisons.** Liquidity, verification, slippage, and profit checks are local `if` statements.
3. **Batch state reads.** Each node reads the full state once at entry, mutates its fields, and writes back. No incremental field reads.
4. **Abort early.** If any deterministic check fails, set `status = ABORTED` and terminate the graph instance immediately. Do not call downstream LLMs.

### 2.2 Pre-Filtering Execution Order

```
1. is_verified == False?  →  ABORT("unverified contract")
2. liq_usd < 2500?        →  ABORT("insufficient liquidity")
3. token_address in blacklist?  →  ABORT("blacklisted address")
4. All pass  →  proceed to LLM audit node
```

### 2.3 Source Code Minification

Before passing contract source to the auditor agent, apply the minifier defined in [`Skills/code_minifier/skill.md`](Skills/code_minifier/skill.md):

1. Strip all `// ...` and `/* ... */` comments.
2. Remove `pragma solidity ...` directives and `interface` / `abstract` descriptors.
3. Collapse all whitespace (tabs, newlines, consecutive spaces) into single spaces.
4. Verify compressed payload is ≥ 60% smaller than raw input. If not, log a warning but proceed.

---

## 3. Agent Profiles

---

### A. Filter Gate Agent (Programmatic Firewall)

**Core Role:** Deterministic data screening — no LLM involved.

**Input State Fields:**
- `token_address: str`
- `pool_address: str`
- `liq_usd: float`
- `is_verified: bool`

**Operational Directives:**

1. Check `is_verified`. If `False`, write `state.status = ABORTED`, `state.reason = "unverified contract bytecode"`, and terminate.
2. Check `liq_usd >= 2500.0`. If `False`, write `state.status = ABORTED`, `state.reason = f"liquidity ${liq_usd:.2f} below $2500 floor"`, and terminate.
3. Check `token_address` against the local blacklist set. If found, write `state.status = ABORTED`, `state.reason = "blacklisted token address"`, and terminate.
4. If all checks pass, mutate `state.status = FILTERED` and route to Agent B.

**State Mutation:**
```python
state.status = Status.FILTERED  # on success
# or
state.status = Status.ABORTED
state.reason = "..."            # on failure
```

---

### B. Smart Contract Auditor Agent (Scam Detection Engine)

**Core Role:** Structural security and honeypot detection via LLM reasoning.

**Input State Fields:**
- `token_address: str` (used to fetch source via Etherscan MCP)

**Pre-Processing:** Fetch source code via Etherscan MCP endpoint, then run the [Code Minifier](Skills/code_minifier/skill.md) to produce `state.minified_source`.

**System Prompt (Gemini API):**

```
You are a Solidity security auditor. You will receive minified smart contract source code.

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
- If the code is too short or empty to audit, set is_safe=false and threats=["insufficient source code"].
```

**Output Pydantic Schema:**

```python
class AuditResult(BaseModel):
    is_safe: bool
    threats: List[str]
```

**State Mutation:**
- Parse the LLM response into `AuditResult`.
- If `is_safe == False`: `state.status = ABORTED`, `state.reason = f"audit failed: {threats}"`.
- If `is_safe == True`: `state.audit_is_safe = True`, `state.audit_threats = []`, `state.status = AUDITED`.

---

### C. Slippage & Quant Analyst Agent (Mathematical Validator)

**Core Role:** Mechanical evaluation of execution decay and fee-adjusted margins — no LLM involved.

**Input State Fields:**
- `trade_size_usd: float`
- `pool_reserve_usd: float` (equivalent to pool liquidity)
- `gross_spread_pct: float`
- `gas_usd: float` (default: `0.02`)

**Mathematical Invariants (executed locally):**

$$
\text{Expected Slippage \%} = \left( \frac{\text{Trade Size USD}}{\text{Pool Liquidity USD}} \right) \times 100
$$

$$
\text{Net Profit USD} = \left( \frac{\text{Gross Spread \%} - \text{Expected Slippage \%}}{100} \right) \times \text{Trade Size USD} - \text{Gas USD}
$$

**Implementation:**

```python
expected_slippage_pct = (state.trade_size_usd / state.pool_reserve_usd) * 100
net_profit_usd = ((state.gross_spread_pct - expected_slippage_pct) / 100) * state.trade_size_usd - state.gas_usd
```

**Validation Bounds:**
- `gas_usd` baseline: `$0.02` (Arbitrum One L2 hardcoded).
- Execution floor: `net_profit_usd >= 0.50`.

**State Mutation:**
- If `net_profit_usd < 0.50`: `state.status = ABORTED`, `state.reason = f"net profit ${net_profit_usd:.4f} below $0.50 floor"`.
- If `net_profit_usd >= 0.50`: `state.expected_slippage_pct = expected_slippage_pct`, `state.net_profit_usd = net_profit_usd`, `state.status = VALIDATED`.

---

### D. Execution Gatekeeper Agent (Secure Settlement Engine)

**Core Role:** Final risk validation and transaction routing authorization.

**Input State Fields:**
- `state.status` (must be `VALIDATED`)
- `state.audit_is_safe` (must be `True`)
- `state.net_profit_usd` (must be `>= 0.50`)
- `state.token_address`
- `state.pool_address`
- `state.trade_size_usd`

**Operational Directives:**

1. Verify `state.status == VALIDATED`. If not, `ABORT("invalid upstream state")`.
2. Verify `state.audit_is_safe == True`. If not, `ABORT("audit not passed")`.
3. Verify `state.net_profit_usd >= 0.50`. If not, `ABORT("profit below execution floor")`.
4. If all checks pass:
   - Mutate `state.status = AUTHORIZED`.
   - Invoke the local transaction builder to construct, sign, and submit the swap transaction.
   - Route via **private RPC bundle relay** (e.g., Flashbots Protect or Arbitrum private mempool) to bypass public mempool sandwich attacks.
   - On successful submission: `state.tx_hash = "<tx_hash>"`, `state.status = EXECUTED`.
   - On failure: `state.status = ABORTED`, `state.reason = "tx submission failed: <error>"`.
5. If any check fails, terminate the state and wipe all in-memory buffers holding contract source or trade parameters.

**State Mutation:**
```python
state.status = Status.AUTHORIZED  # pre-tx
# ... sign and submit ...
state.status = Status.EXECUTED
state.tx_hash = "0x..."
# or
state.status = Status.ABORTED
state.reason = "..."
```

---

## 4. Operational Quick Reference

| Check | Type | Threshold | Node |
|---|---|---|---|
| `is_verified` | boolean | `True` | A |
| `liq_usd` | float | `>= $2,500` | A |
| blacklist match | set lookup | not found | A |
| `audit_is_safe` | boolean | `True` | B |
| `net_profit_usd` | float | `>= $0.50` | C |
| `gas_usd` baseline | float | `$0.02` | C |

**Command to run full pipeline (when implemented):**
```bash
python -m longtail.pipeline --run-id <id> --token <address> --pool <address>
```

**Key directories:**
- `Skills/` — Agent skill definitions (minifier, filter gate, slippage math)
- `longtail/` — Core pipeline code (to be implemented)
