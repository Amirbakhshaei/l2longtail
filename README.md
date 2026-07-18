---
title: L2longtail
emoji: 👀
colorFrom: red
colorTo: gray
sdk: gradio
sdk_version: 6.20.0
python_version: '3.12'
app_file: app.py
app_port: 7860
pinned: false
---

Check out the configuration reference at https://huggingface.co/docs/hub/spaces-config-reference
# Long-Tail Arbitrage System
Asynchronous multi-agent pipeline tracking and extracting micro-cap cross-DEX inefficiencies.

## Hugging Face Spaces deployment
- **SDK:** Gradio. `app.py` is the entrypoint and owns the UI thread.
- **Hardware:** requires a **ZeroGPU** allocation (`@spaces.GPU` gatekeeper in `app.py`). The arbitrage engine itself runs on CPU.
- **Dependencies:** `gradio`, `spaces`, and `torch` are pre-installed by the platform — do **not** pin them in `requirements.txt`. All engine deps live there.
- **Persistent state** lives under `/data` (logs, `cleared_tokens.db`). Configure Space **Secrets/Variables** (not a committed `.env`):
  `DRY_RUN`, `ANKR_API_KEY`, `LLM_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `EXECUTION_RPCS`.
  For live mode also set `KEYSTORE_PATH=/data/keystore.json` and upload the keystore to `/data`.
- **Rust hot-path:** `src/lib.rs` (`alloy_executor`) compiles locally via `maturin develop`. The Space build has no Rust toolchain, so it transparently falls back to the pure-Python `broadcast_raw_tx` path.