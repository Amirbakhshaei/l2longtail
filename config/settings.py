from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    dry_run: bool = Field(default=True, description="True = simulate, False = sign and broadcast")

    ankr_api_key: str = ""
    ankr_rpc_url: str = ""
    fallback_rpc_url: str = "https://arb1.arbitrum.io/rpc"
    flashbots_rpc_url: str = "https://rpc.flashbots.net/fast"
    wss_rpc_url: str = ""
    # Sync transport: "auto" (WSS when WSS_RPC_URL is set, else HTTP polling),
    # "wss" (require WebSocket), or "http" (eth_getLogs polling over HTTPS RPC).
    sync_transport: str = "auto"
    sync_poll_interval: float = 4.0   # seconds between eth_getLogs polls
    sync_poll_blocks: int = 5         # blocks of history fetched per poll

    llm_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model_primary: str = "llama-3.3-70b-versatile"
    llm_model_fallback: str = "gemma2-9b-it"
    llm_max_retries: int = 5
    llm_temperature: float = 0.0

    arbiscan_api_key: str = ""

    keystore_path: str = "./keystore.json"
    keystore_passphrase: str = ""

    min_liquidity_usd: float = 100.0
    max_liquidity_usd: float = 100000.0
    min_spread_pct: float = 1.0
    min_net_profit_usd: float = 0.50
    gas_baseline_usd: float = 0.02
    max_trade_size_usd: float = 10.0

    whitelist_path: str = "config/whitelist.json"
    sync_lookback_blocks: int = 50

    flashloan_executor_address: str = ""
    balancer_vault_address: str = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"

    dexscreener_rate_limit: float = 3.0
    scanner_scan_interval: int = 30

    max_concurrent_graphs: int = 5
    rpc_rate_limit_per_sec: int = 10
    cache_ttl_hours: int = 24
    db_path: str = "longtail.db"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    prometheus_port: int = 9090
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def model_post_init(self, __context: object) -> None:
        if not self.ankr_rpc_url:
            self.ankr_rpc_url = (
                f"https://rpc.ankr.com/arbitrum/{self.ankr_api_key}"
                if self.ankr_api_key
                else "https://rpc.ankr.com/arbitrum"
            )
        # Normalize / derive the WebSocket endpoint. This runs even when an
        # explicit WSS URL is supplied, so a malformed value (e.g. a leftover
        # http(s):// RPC, a doubled scheme like "wss://wss://...", or an Ankr
        # key appended to the wrong base) is corrected instead of crashing the
        # client with "isn't a valid URI: scheme isn't ws or wss".
        self.wss_rpc_url = self._normalize_wss_url(
            self.wss_rpc_url, self.fallback_rpc_url, self.ankr_rpc_url, self.ankr_api_key
        )

    @staticmethod
    def _normalize_wss_url(
        wss: str, fallback_rpc: str, ankr_rpc: str, ankr_key: str
    ) -> str:
        """Return a guaranteed ws:// or wss:// URI for the Arbitrum node.

        Priority:
          1. An explicit WSS_RPC_URL override (normalized). This is the
             recommended path: point it at a provider that supports the
             JSON-RPC `eth_subscribe` method (Alchemy, Infura, QuickNode, or
             Ankr's wss://apis.ankr.com/wss/... endpoint).
          2. Last resort: the public Arbitrum *sequencer feed*
             (wss://arb1-feed.arbitrum.io/feed). This connects without auth,
             but speaks the sequencer batch protocol rather than eth_subscribe,
             so the listener must run in feed mode for it to deliver swap
             events. It is only used when no explicit WSS_RPC_URL is set.

        NOTE: Arbitrum's public RPC (wss://arb1.arbitrum.io/ws) does NOT
        provide WebSocket support and returns HTTP 401 — it is intentionally
        not used here.
        """
        # 1) Explicit WSS override (normalized) — takes precedence. This is the
        #    recommended path: point WSS_RPC_URL at a provider that supports the
        #    JSON-RPC `eth_subscribe` method (Alchemy, Infura, QuickNode, or
        #    Ankr's wss://apis.ankr.com/wss/... endpoint).
        cand = (wss or "").strip()
        if cand:
            norm = Settings._wss_from_host(cand)
            if norm:
                return norm

        # 2) Last resort: the public Arbitrum *sequencer feed*
        #    (wss://arb1-feed.arbitrum.io/feed). This connects without auth and
        #    is the only WebSocket Arbitrum exposes publicly. It speaks the
        #    sequencer batch protocol rather than eth_subscribe, so the listener
        #    must run in feed mode for it to deliver swap events. The HTTPS RPC
        #    host (fallback_rpc_url) is deliberately NOT used: Arbitrum's public
        #    RPC does not provide WebSocket support and returns HTTP 401.
        return "wss://arb1-feed.arbitrum.io/feed"

    @staticmethod
    def _wss_from_host(value: str) -> str:
        """Normalize a WSS input to a valid wss:// URI, preserving any path.

        - Bare host / http(s):// RPC / doubled scheme -> wss://<host>/ws
        - Already-valid wss:// (e.g. Alchemy/Infura .../ws/<key>) -> kept as-is
          (the trailing /<key> path is required by those providers).
        """
        cand = (value or "").strip()
        if not cand:
            return ""
        # Collapse any doubled/incorrect scheme noise first (wss://https://,
        # wss://wss://, http://) down to a single scheme-or-host token.
        while True:
            stripped = cand.split("://", 1)
            if len(stripped) == 2 and not cand.startswith(("ws://", "wss://")):
                cand = stripped[1]
                continue
            # A wss:// with another scheme nested after it -> drop the inner.
            if cand.startswith(("ws://", "wss://")):
                rest = cand.split("://", 1)[1]
                if "://" in rest:
                    cand = "wss://" + rest.split("://", 1)[1]
                    continue
            break
        # Already a valid ws/wss URI (possibly with a path) -> keep it.
        if cand.startswith(("wss://", "ws://")):
            return cand
        # Split host from path; rebuild as wss://<host><path> (default /ws).
        # This keeps provider paths like /ws/<key> for Alchemy/Infura/QuickNode.
        parts = cand.split("/", 1)
        host = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        if not host:
            return ""
        if path:
            return f"wss://{host}/{path}"
        return f"wss://{host}/ws"
