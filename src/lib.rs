//! alloy_executor — native Rust hot-path for the Arbitrum long-tail MEV engine.
//!
//! # Architecture boundary (see rust_bridge.md)
//! ---------------------------------------------------------------------------
//! * Python domain: graph indexing, state cache, loop management, config,
//!   cycle discovery (`process_a_indexer.py`, `process_b_sniper.py`,
//!   `infra/websocket_listener.py`).
//! * Rust domain (THIS crate, compiled to `alloy_executor` via Maturin):
//!   cryptographic signing, ABI/byte packing, payload serialization, and
//!   asynchronous multi-RPC broadcasting.
//!
//! Nothing in this crate must ever be re-implemented in slow Python on the
//! hot path. The Python baseline `RPCManager.broadcast_raw_tx` exists only as
//! a fallback when the native extension is unavailable.
//! ---------------------------------------------------------------------------
//!
//! `ex_broadcast` releases the Python GIL via `Python::allow_threads` so the
//! Tokio runtime executes on raw OS threads, then:
//!   1. packs the V3 route with `encode_packed` (address,uint24,address,...),
//!   2. signs the EVM tx locally with Alloy's native signer (network base
//!      fee — no PGA/bribe),
//!   3. blasts `eth_sendRawTransaction` to every RPC URL concurrently.

use alloy::{
    network::{Ethereum, EthereumWallet, TransactionBuilder},
    primitives::{Address, Bytes, TxKind, U256},
    providers::{Provider, ProviderBuilder, RootProvider},
    rpc::types::request::TransactionRequest,
    signers::local::PrivateKeySigner,
};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use std::sync::Arc;
use tokio::runtime::Runtime;
use tokio::task;

/// Pack a Uniswap-V3-style route exactly like `eth_abi.packed.encode_packed`
/// in `infra/onchain_pricing.py`:
///
/// ```text
/// types  = ['address', 'uint24', 'address', ..., 'uint24', 'address']
/// values = [tok0, fee0*100, tok1, fee1*100, ..., tokN]
/// ```
///
/// `fees[i]` is the per-hop fee tier in basis points; the V3 router expects
/// the fee as 1e-6 of notional, hence `fee * 100`.
fn pack_path(path_tokens: &[String], fees: &[u32]) -> PyResult<Bytes> {
    if path_tokens.len() != fees.len() + 1 {
        return Err(PyValueError::new_err(format!(
            "tokens ({}) must be exactly one longer than fees ({})",
            path_tokens.len(),
            fees.len()
        )));
    }

    let mut out: Vec<u8> = Vec::with_capacity(path_tokens.len() * 20 + fees.len() * 3);
    for (i, tok) in path_tokens.iter().enumerate() {
        let addr: Address = tok
            .parse()
            .map_err(|e| PyValueError::new_err(format!("bad token addr {tok}: {e}")))?;
        out.extend_from_slice(addr.as_slice()); // 20-byte address
        if i < fees.len() {
            let fee: u32 = fees[i] * 100; // bps -> V3 fee unit
            out.extend_from_slice(&fee.to_be_bytes()[1..]); // 3-byte uint24
        }
    }
    Ok(Bytes::from(out))
}

/// Build the full EVM transaction (EIP-1559) carrying the packed route as its
/// calldata. `to` is the router/executor target; `amount_in` is parsed as a
/// decimal wei string. Mirrors the Python `LiveExecutor.sign_calldata` payload.
fn build_tx(
    to: Address,
    packed: Bytes,
    amount_in: &str,
    chain_id: u64,
) -> PyResult<TransactionRequest> {
    let value: U256 = amount_in
        .parse()
        .map_err(|e| PyValueError::new_err(format!("bad amount_in: {e}")))?;

    let mut tx = TransactionRequest::default();
    tx.to = Some(TxKind::Call(to));
    tx.input = packed.into();
    tx.value = Some(value);
    tx.chain_id = Some(chain_id);
    tx.gas = Some(600_000);
    // max_fee_per_gas / max_priority_fee_per_gas left unset -> the signer
    // fills them at the network base fee (no PGA/bribe).
    Ok(tx)
}

/// Fire one `eth_sendRawTransaction` to a single RPC URL on the Tokio pool.
/// Returns `Ok(tx_hash)` on acceptance, `Err(reason)` otherwise. Collision
/// markers ("already known", "nonce too low") are returned as `Err` and
/// swallowed by the Python caller.
async fn send_one(provider: Arc<RootProvider<Ethereum>>, raw: Bytes) -> Result<String, String> {
    match provider.send_raw_transaction(raw).await {
        Ok(pending) => Ok(format!("{:#x}", pending.tx_hash())),
        Err(e) => Err(format!("{e}")),
    }
}

/// # Hybrid hot-path entry point (exposed to Python as `alloy_executor.ex_broadcast`).
///
/// Packs the route, signs locally (base fee, no PGA/bribe), and broadcasts the
/// signed raw tx concurrently to every RPC URL. The GIL is released for the
/// entire Rust execution so Tokio runs unhindered by the Python interpreter.
#[pyfunction]
#[pyo3(signature = (path_tokens, fees, amount_in, private_key, rpc_urls, to_address, chain_id=42161))]
fn ex_broadcast(
    py: Python<'_>,
    path_tokens: Vec<String>,
    fees: Vec<u32>,
    amount_in: String,
    private_key: String,
    rpc_urls: Vec<String>,
    to_address: String,
    chain_id: u64,
) -> PyResult<Vec<Option<String>>> {
    // Release the GIL: everything below runs on native Tokio worker threads.
    let result: PyResult<Vec<Option<String>>> = py.allow_threads(|| {
        let rt = Runtime::new()
            .map_err(|e| PyRuntimeError::new_err(format!("tokio rt: {e}")))?;

        rt.block_on(async move {
            // 1) Pack the V3 route (encode_packed equivalent).
            let packed = pack_path(&path_tokens, &fees)?;
            let to: Address = to_address
                .parse()
                .map_err(|e| PyValueError::new_err(format!("bad to_address: {e}")))?;

            // 2) Construct + sign locally with Alloy's native signer, filled
            //    at the network base fee (no PGA/bribe logic).
            let signer: PrivateKeySigner = private_key
                .parse()
                .map_err(|e| PyValueError::new_err(format!("bad key: {e}")))?;
            let wallet = EthereumWallet::from(signer);

            let tx = build_tx(to, packed, &amount_in, chain_id)?;
            // `fill` resolves gas / fee / nonce from the network base fee;
            // `seal` applies the local signature; `encoded` yields RLP bytes.
            let filled = wallet.fill(tx).await
                .map_err(|e| PyRuntimeError::new_err(format!("fill: {e}")))?;
            let sealed = filled
                .seal(&wallet)
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("seal: {e}")))?;
            let raw: Bytes = sealed.encoded().into();

            // 3) Blast to every RPC concurrently on the Tokio thread pool.
            let mut handles = Vec::with_capacity(rpc_urls.len());
            for url in rpc_urls {
                let raw = raw.clone();
                let handle = task::spawn(async move {
                    let provider: RootProvider<Ethereum> =
                        ProviderBuilder::new().on_http(url.parse().unwrap());
                    match send_one(Arc::new(provider), raw).await {
                        Ok(h) => Some(h),
                        Err(_) => None, // collisions / transport errors -> silent
                    }
                });
                handles.push(handle);
            }

            let mut out: Vec<Option<String>> = Vec::with_capacity(handles.len());
            for h in handles {
                out.push(h.await.ok().flatten());
            }
            Ok(out)
        })
    });

    result
}

/// Module definition — Maturin builds this as the `alloy_executor` extension.
#[pymodule]
fn alloy_executor(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(ex_broadcast, m)?)?;
    Ok(())
}
