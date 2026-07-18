# Rust ⇄ Python Architecture Bridge (HYBRID MEV ENGINE)

> **PERMANENT MEMORY ANCHOR.** This document records the split-language
> boundary of the Arbitrum long-tail MEV engine. It MUST be consulted before
> any refactoring that touches the execution hot path. Do **not** move compiled
> Rust logic back into slow Python on the hot path.

## 1. Language domain split

| Domain | Responsibility | Files |
|--------|-----------------|-------|
| **Python** | Graph indexing, state cache, event loop management, configuration, cycle discovery | `process_a_indexer.py`, `process_b_sniper.py`, `infra/websocket_listener.py`, `config/*`, `infra/rpc_manager.py` (fallback baseline) |
| **Rust** | Cryptographic signing, ABI/byte packing, payload serialization, asynchronous multi-RPC broadcasting | `src/lib.rs` → compiled to `alloy_executor` via Maturin |

## 2. Cross-compilation boundary

- `src/lib.rs` is compiled to a `cdylib` by **Maturin** and exposed to
  Python as the module **`alloy_executor`**.
- The single exposed function is
  `alloy_executor.ex_broadcast(path_tokens, fees, amount_in, private_key, rpc_urls, to_address, chain_id=42161)`.
- `ex_broadcast` releases the Python **GIL** via `Python::allow_threads` for
  its entire body, so Alloy/Tokio execute on raw OS threads.
- In Rust: packed-path encoding mirrors `eth_abi.packed.encode_packed`
  (`[address, uint24(fee*100), address, ...]`) from
  `infra/onchain_pricing.py:pack_path`. Keep this byte-layout in lock-step
  with the Python reference or quotes will desync.

## 3. Fallback chain (no native module present)

1. **Primary:** `alloy_executor.ex_broadcast` (native, GIL-free).
2. **Fallback:** `RPCManager.broadcast_raw_tx` in `infra/rpc_manager.py`
   (pure-Python asyncio.gather shotgun). Used only when the extension is
   not importable.

## 4. Invariant rules for future refactors

- ❌ Never re-implement `ex_broadcast`'s signing/packing/broadcast loop in
  Python for the live path.
- ❌ Never block the indexer loop on broadcast — both paths are
  fire-and-forget (`asyncio.create_task`).
- ✅ If the route schema changes, update BOTH `pack_path` (Rust) and
  `infra/onchain_pricing.py` (Python) together.
- ✅ Collision errors ("already known", "nonce too low") are expected from
  late-arriving peers and MUST be swallowed silently at the boundary.

## 5. Build & deploy

```bash
# Dev (install extension into active venv)
maturin develop

# Release wheel
maturin build --release

# Env prerequisites: rustc + cargo (stable), maturin (see requirements.txt)
```
