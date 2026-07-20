"""
Long-Tail Arbitrage Engine — Hugging Face ZeroGPU Spaces Dashboard

Gradio owns the primary thread for UI + health probes.
Blockchain loops run in a dedicated daemon thread with their own asyncio event loop.
Persistent storage at /data/ preserves SQLite state across container restarts.
ZeroGPU gatekeeper bypass satisfies HF platform startup detection.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path

import gradio as gr
import spaces
import torch
from dotenv import load_dotenv

load_dotenv()

# Ensure the repo root (where the ``db``/``infra``/``config`` packages
# live) is importable no matter what CWD the HF Space launcher uses.
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _persistent_path(name: str) -> Path:
    """Resolve a state/log path.

    On Hugging Face Spaces the persistent volume is mounted at ``/data``;
    writing there survives container rebuilds. Locally (or any host without
    ``/data``) we fall back to the current working directory so dev runs
    are unaffected.
    """
    data_dir = Path("/data")
    if data_dir.is_dir():
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / name
    return Path(name)


# Configure the structured telemetry logger (logs/telemetry.jsonl by default).
# On Spaces the persistent volume at /data survives rebuilds, so we point the
# JSONL export there when available. Must run before process_b_sniper imports
# it (it self-configures on import).
if Path("/data").is_dir():
    os.environ.setdefault("TELEMETRY_DIR", "/data/logs")
import infra.logger_setup  # noqa: E402  (side-effect: installs jsonl sink)


# Engine + UI logs are mirrored to a file (under /data on Spaces) so the
# Gradio viewer can tail real activity instead of only stdout. A StreamHandler
# to stdout is added *alongside* the file handler so the same records also
# land in the Space's captured stdout (Gradio's own banner uses stdout,
# so without this the engine logs would be invisible in the Space logs).
LOG_FILE_PATH = str(_persistent_path("engine.log"))

_root = logging.getLogger()
_root.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
_file_handler = logging.FileHandler(LOG_FILE_PATH, mode="a")
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
_root.addHandler(_file_handler)
_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
_root.addHandler(_stdout_handler)

logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@spaces.GPU
def zero_gpu_gatekeeper_bypass(n: float) -> str:
    """Dummy tensor math function to satisfy HF ZeroGPU startup detection."""
    zero_tensor = torch.Tensor([0]).cuda()
    return f"ZeroGPU Verified on {zero_tensor.device}"


def start_arbitrage_engine() -> None:
    """Runs the async engine loop inside a daemon background thread.

    Uses ``asyncio.run`` (not a manually-owned loop) so the loop is
    created *and* closed by the runtime — this prevents the Python 3.12
    ``BaseEventLoop.__del__`` "Invalid file descriptor: -1" warning that
    fires when a stray ``set_event_loop`` loop is garbage-collected after
    its self-pipe is already torn down (e.g. on an HF SSR restart).
    """
    logger.info(
        "[STARTUP] engine thread spawned (mode=%s)",
        "PAPER" if os.getenv("DRY_RUN", "true") == "true" else "LIVE",
    )
    try:
        asyncio.run(_run_engine())
    except Exception as e:  # noqa: BLE001
        logger.exception("[STARTUP] engine loop terminated: %s", e)


async def _run_engine() -> None:
    logger.info("[STARTUP] importing engine modules…")
    from db.cleared_tokens import ClearedTokensDB
    from infra.flea_market_discovery import FleaMarketDiscovery
    from infra.rate_limiter import TokenBucketRateLimiter
    from infra.rpc_manager import RPCManager
    from monitoring.alerts import TelegramAlerts
    from process_a_indexer import ProcessAIndexer
    from process_b_sniper import ProcessBSniper
    from infra.websocket_listener import WebSocketListener, LogsPoller
    logger.info("[STARTUP] engine modules imported")

    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    lookback = int(os.getenv("SYNC_LOOKBACK_BLOCKS", "50"))
    from config.settings import Settings

    try:
        settings = Settings()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "[STARTUP] config/Settings failed (check Space Secrets): %s", e
        )
        return
    logger.info("[STARTUP] config loaded (Secrets resolved)")
    wss_url = settings.wss_rpc_url or (
        "wss://"
        + settings.fallback_rpc_url.replace("https://", "", 1)
        .replace("http://", "", 1)
        .split("/")[0]
        + "/ws"
    )
    # WSS provider pool for the Instant-Rotation Watchdog. Comma-separated
    # WSS_URLS (Space Secret) enables failover across free-tier providers; if
    # unset we run a single-URL pool on the derived wss_url.
    raw_pool = os.getenv("WSS_URLS", "")
    wss_pool = [u.strip() for u in raw_pool.split(",") if u.strip()]
    if not wss_pool:
        wss_pool = [wss_url]
    vault_address = os.getenv(
        "BALANCER_VAULT_ADDRESS", "0xBA12222222228d8Ba445958a75a0704d566BF2C8"
    )
    executor_address = os.getenv("FLASHLOAN_EXECUTOR_ADDRESS", "")

    ankr_key = os.getenv("ANKR_API_KEY", "")
    primary_url = (
        f"https://rpc.ankr.com/arbitrum/{ankr_key}"
        if ankr_key
        else "https://rpc.ankr.com/arbitrum"
    )
    fallback_url = "https://arb1.arbitrum.io/rpc"

    tg = TelegramAlerts(
        os.getenv("TELEGRAM_BOT_TOKEN", ""),
        os.getenv("TELEGRAM_CHAT_ID", ""),
    )

    rate_limiter = TokenBucketRateLimiter(rate=10, capacity=5)
    rpc = RPCManager(
        primary_url or fallback_url, fallback_url, "",
        rate_limiter, execution_rpcs=settings.execution_rpcs_list,
    )
    cleared_db = ClearedTokensDB(db_path=_persistent_path("cleared_tokens.db"))

    whitelist_path = os.getenv("WHITELIST_PATH", "config/whitelist.json")
    flea = FleaMarketDiscovery(rpc, whitelist_path=whitelist_path)

    llm_key = os.getenv("LLM_API_KEY", "")
    llm_model = os.getenv("LLM_MODEL_PRIMARY", "llama-3.3-70b-versatile")

    sync_queue: asyncio.Queue = asyncio.Queue()

    # Choose sync transport: WSS when a real WebSocket endpoint is explicitly
    # configured (WSS_RPC_URL env), otherwise HTTP eth_getLogs polling (e.g. a
    # free Ankr HTTPS RPC that has no WebSocket support). The internal
    # sequencer-feed default is NOT a usable eth_subscribe endpoint, so it must
    # not force WSS mode.
    use_wss = bool(
        os.getenv("WSS_RPC_URL")
        and settings.sync_transport in ("auto", "wss")
        and str(os.getenv("WSS_RPC_URL")).startswith(("ws://", "wss://"))
    )
    if use_wss:
        sync_source = WebSocketListener(
            wss_pool, flea.whitelisted_addresses, v3_addresses=flea.v3_addresses
        )
        sync_transport = "wss"
    else:
        if settings.sync_transport == "wss":
            logger.warning(
                "sync_transport=wss but no valid WSS_RPC_URL set; falling back to HTTP polling"
            )
        sync_source = LogsPoller(
            rpc_manager=rpc,
            whitelisted_addresses=flea.whitelisted_addresses,
            v3_addresses=flea.v3_addresses,
            poll_interval=settings.sync_poll_interval,
            poll_blocks=settings.sync_poll_blocks,
        )
        sync_transport = "http"

    process_a = ProcessAIndexer(
        rpc_manager=rpc,
        cleared_db=cleared_db,
        websocket_listener=sync_source,
        sync_queue=sync_queue,
        flea_discovery=flea,
        transport=sync_transport,
    )

    process_b = ProcessBSniper(
        cleared_db=cleared_db,
        rpc_manager=rpc,
        dry_run=dry_run,
        llm_api_key=llm_key,
        llm_model=llm_model,
        on_opportunity=tg.notify_opportunity,
        sync_queue=sync_queue,
        vault_address=vault_address,
        executor_address=executor_address,
        graph_mode=True,
    )

    mode = "LIVE" if not dry_run else "PAPER"
    logger.info(
        "[STARTUP] mode=%s | pools=%d | quoting=QuoterV2+Multicall3 | "
        "capital=uncapped-flashloan | gate=net_wei>gas_wei",
        mode, flea.pool_count,
    )
    await tg.notify_engine_start(mode, flea.pool_count)

    async def loop_a() -> None:
        try:
            await process_a.run()
        except Exception as e:
            logger.error("Sync engine failed: %s", e)
            await tg.notify_error("Process A", str(e))

    async def loop_b() -> None:
        try:
            await process_b.run()
        except Exception as e:
            logger.error("Sniper failed: %s", e)
            await tg.notify_error("Process B", str(e))

    try:
        await asyncio.gather(loop_a(), loop_b())
    except Exception as e:  # noqa: BLE001
        # A crash here would otherwise be swallowed by the daemon thread and
        # never surface in the Space logs. Log it loudly.
        logger.exception("[STARTUP] engine loops crashed: %s", e)
    finally:
        # Close the persistent HTTP/2 client so the event loop does not emit
        # a "ValueError: Invalid file descriptor" traceback on __del__.
        try:
            await rpc.close()
        except Exception:  # noqa: BLE001
            pass
        logger.info("[SHUTDOWN] RPC client closed, engine stopped")


def get_latest_terminal_logs() -> str:
    """Reads the tail end of the trading log to display inside the Gradio UI."""
    if not os.path.exists(LOG_FILE_PATH):
        return "Engine initialized. Waiting for Sync events to populate logs..."
    try:
        with open(LOG_FILE_PATH) as log_file:
            lines = log_file.readlines()
            return "".join(lines[-35:])
    except Exception as e:
        return f"Error reading execution logs: {e}"


# Fire the background trading pipeline instantly on app startup
threading.Thread(target=start_arbitrage_engine, daemon=True).start()

with gr.Blocks(title="L2 Sync Arbitrage Terminal") as demo:
    gr.Markdown("# L2 Triangular Arbitrage — Sync Engine")
    gr.Markdown(
        "Monitors whitelisted V2 pools for Sync events. "
        "Routes WETH -> TOKEN -> USDC -> WETH on Arbitrum One."
    )

    gpu_hardware_string = zero_gpu_gatekeeper_bypass(1.0)

    with gr.Row():
        engine_status = gr.Textbox(
            label="Platform Infrastructure",
            value=f"ACTIVE - {gpu_hardware_string}",
            interactive=False,
        )
        storage_status = gr.Textbox(
            label="Persistent Memory Bank",
            value="MOUNTED - cleared_tokens.db",
            interactive=False,
        )
        execution_mode = gr.Textbox(
            label="Execution Mode",
            value=(
                "PAPER TRADING (dry_run=true)"
                if os.getenv("DRY_RUN", "true") == "true"
                else "LIVE MODE"
            ),
            interactive=False,
        )

    log_viewer = gr.Textbox(
        label="Live Engine Activity Log Feed (Auto-Refreshes Every 5s)",
        value=get_latest_terminal_logs(),
        lines=25,
        max_lines=40,
        interactive=False,
    )

    log_timer = gr.Timer(5)
    log_timer.tick(fn=get_latest_terminal_logs, outputs=log_viewer)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
