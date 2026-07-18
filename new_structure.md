# System Role: Principal Hybrid Systems Architect
# Core Objective
We are moving our Arbitrum long-tail MEV engine to a hybrid Python-Rust architecture to maximize execution speed on a First-Come, First-Served (FCFS) sequencer. 

You must implement a multi-RPC simultaneous broadcasting engine in Python as a baseline, and then systematically migrate the byte-packing, signing, and broadcasting hot path into a high-performance native Rust extension using `PyO3` and the `alloy` framework.

---

## Part 1: Architecture Memory & State Law (CRITICAL)
You must establish a permanent memory anchor regarding our split-language architecture. For this request and all subsequent tasks:
1. **Python Domain:** Graph indexing, state cache, loop management, configuration, and cycle discovery (`process_a_indexer.py`, `process_b_sniper.py`, `infra/websocket_listener.py`).
2. **Rust Domain:** Cryptographic operations, ABI packing, payload serialization, and asynchronous RPC broadcasting (`src/lib.rs` compiled to a native module via Maturin).
3. **Tracking:** You must maintain a `rust_bridge.md` or a clear architecture comment block at the top of modified files detailing exactly which boundaries are cross-compiled so that no future refactoring attempts to move compiled logic back into slow Python code.

---

## Part 2: Phase 1 — Multi-RPC Simultaneous Python Baseline

1. **Modify `infra/rpc_manager.py`:**
   * Update the initialization to pull an array of strings from the environment representing multiple free-tier RPC nodes (e.g., Alchemy, Ankr, dRPC).
   * Implement an asynchronous broadcasting method:
     ```python
     async def broadcast_raw_tx(self, signed_tx_hex: str) -> list:
         # Execute eth_sendRawTransaction concurrently across ALL endpoints
         # Wrap in asyncio.gather with return_exceptions=True
         # Silently handle and ignore expected duplicate transaction or nonce errors 
         # from late-arriving endpoints (e.g., "already known", "nonce too low")
     ```

2. **Modify `agents/process_b_sniper.py`:**
   * In the path evaluation loop, when a profitable trade is found (`net_profit_wei > 0`), clear out any legacy Priority Gas Auction (PGA) or miner bribing parameters. Use the network base fee.
   * Sign the transaction locally using `w3.eth.account.sign_transaction`.
   * Trigger the broadcast using a detached, fire-and-forget task: 
     `asyncio.create_task(self.rpc_manager.broadcast_raw_tx(signed_tx.raw_transaction.hex()))`

---

## Part 3: Phase 2 — PyO3 + Alloy Rust Hot-Path Injection

1. **Initialize Maturin Environment:**
   * Create a cargo manifest (`Cargo.toml`) configuring a `cdylib` crate targeting `pyo3`.
   * Add dependencies: `pyo3 = { version = "*", features = ["extension-module"] }`, `tokio = { version = "*", features = ["full"] }`, and `alloy = { version = "*", features = ["providers", "signers", "rpc-types"] }`.

2. **Develop the Native Rust Core (`src/lib.rs`):**
   * Write a function `ex_broadcast(path_tokens: Vec<String>, fees: Vec<u32>, amount_in: String, private_key: String, rpc_urls: Vec<String>)`.
   * Inside this function, immediately release the Python Global Interpreter Lock (GIL) using `Python::allow_threads` so the Rust runtime executes on raw hardware threads unhindered by Python.
   * Implement packed path encoding identical to `eth_abi.packed.encode_packed`.
   * Sign the resulting EVM transaction object locally using Alloy's hardware-optimized private key structures.
   * Spawn a native Tokio thread pool to blast `eth_sendRawTransaction` via raw HTTP requests directly to all `rpc_urls` concurrently.

3. **Expose to Python:**
   * Expose the function via `#[pyfunction]` inside a `#[pymodule]` block named `alloy_executor`.
   * Update `requirements.txt` to include `maturin`.

---

# Output Requirements
1. The updated multi-RPC asynchronous `broadcast_raw_tx` method in Python.
2. The complete Rust `src/lib.rs` using PyO3 and Alloy.
3. The configuration `Cargo.toml` file.
Do not provide generic filler text or conversational pleasantries. Proceed directly to building the structural code blocks.