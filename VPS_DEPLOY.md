# VPS Deployment Checklist

Headless deployment of the Long-Tail Arbitrage Engine on an Arbitrum VPS
(HTTP polling transport, uncapped flashloan capital).

## 1. Provision the box
- OS: Ubuntu 22.04+ (or any systemd Linux).
- Python: **3.12** (matches `pyproject.toml` / README; `target-version = py312`).
- Open outbound TCP 443 (HTTPS RPC) + the Prometheus port (default 9090).
- Non-root user `longtail` with sudo.

```bash
sudo useradd -m -s /bin/bash longtail
sudo mkdir -p /opt/longtail && sudo chown -R longtail:longtail /opt/longtail
```

## 2. Install code + venv
```bash
sudo -u longtail git clone <repo> /opt/longtail
cd /opt/longtail
sudo -u longtail python3.12 -m venv venv
sudo -u longtail ./venv/bin/python -m pip install -U pip
sudo -u longtail ./venv/bin/python -m pip install -r requirements.txt
```
> `requirements.txt` now pins `eth-abi`, `eth-utils`, and `py-solc-x`
> (needed by `scripts/deploy_executor.py`). The gradio/torch Spaces UI
> deps are commented out — not required headless.

## 3. Configure environment
```bash
sudo -u longtail cp .env.example /opt/longtail/.env
sudo -u longtail nano /opt/longtail/.env
```
Minimum required for a **PAPER** run:
- `DRY_RUN=true`
- `ANKR_API_KEY` (or leave blank → public Ankr, rate-limited)
- `LLM_API_KEY` (for the scam auditor; engine still runs if blank)
- `WHITELIST_PATH=config/whitelist.json`

Required for **LIVE** (in addition):
- `DRY_RUN=false`
- `KEYSTORE_PATH` + `KEYSTORE_PASSPHRASE` (funded Arbitrum wallet)
- `FLASHLOAN_EXECUTOR_ADDRESS` (deploy it first — step 4)

## 4. Seed the whitelist (pure V2 + V3, fee tiers saved)
```bash
sudo -u longtail ./venv/bin/python -m scripts.seed_whitelist
```
Verifies `config/whitelist.json` contains pools with `fee_tier` for V3.

## 5. Deploy the flashloan executor (LIVE only)
```bash
sudo -u longtail ./venv/bin/python scripts/deploy_executor.py
# writes FLASHLOAN_EXECUTOR_ADDRESS into .env on success
```
Requires `DRY_RUN=false`, a funded keystore, and a working RPC.

## 6. Smoke test (PAPER first, always)
```bash
sudo -u longtail DRY_RUN=true ./venv/bin/python -m scripts.run_live
```
Watch logs for `Engine ready`, `Process A: ... Sync Engine started`,
and `GRAPH CYCLE:` lines. Confirm no `AttributeError` / import errors.

## 7. Install systemd service
The unit lives at `systemd/longtail.service` and assumes the repo at
`/opt/longtail` with a venv at `/opt/longtail/venv`.

```bash
sudo cp systemd/longtail.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now longtail
sudo journalctl -u longtail -f
```

## 8. Pre-flight guardrails
- [ ] `DRY_RUN=true` validated for ≥ a few minutes before going LIVE.
- [ ] `FLASHLOAN_EXECUTOR_ADDRESS` set and contract verified on-chain.
- [ ] Keystore wallet funded with ETH for gas only (flashloan covers capital).
- [ ] `WSS_RPC_URL` empty → engine uses `LogsPoller` HTTP polling (1.5s).
- [ ] Prometheus metrics reachable on `PROMETHEUS_PORT`.
- [ ] Telegram alerts configured (optional but recommended for LIVE).

## Transport note
The engine is **transport-agnostic**: `ProcessAIndexer` drives whichever
sync source is selected (`WebSocketListener` if `WSS_RPC_URL` is a valid
`wss://`, else `LogsPoller`). Per the directive we stay on HTTP polling to
avoid WSS paywalls; idle HTTP/2 resets are recovered silently (no warning
log, connection reset, continue).
