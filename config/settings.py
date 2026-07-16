from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    dry_run: bool = Field(default=True, description="True = simulate, False = sign and broadcast")

    ankr_api_key: str = ""
    ankr_rpc_url: str = ""
    fallback_rpc_url: str = "https://arb1.arbitrum.io/rpc"
    flashbots_rpc_url: str = "https://rpc.flashbots.net/fast"
    wss_rpc_url: str = ""

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
          1. An explicit WSS_RPC_URL override (normalized).
          2. Ankr keyed WSS (wss://rpc.ankr.com/arbitrum/ws/<key>) when an API
             key is present — this is the authenticated endpoint Arbitrum's
             public gateway (wss://arb1.arbitrum.io/ws) rejects with HTTP 401.
          3. Derive wss://<host>/ws from the HTTPS fallback RPC host.
        Any input is stripped of doubled/incorrect schemes before use.
        """
        # 1) Explicit WSS override (normalized) — takes precedence.
        cand = (wss or "").strip()
        if cand:
            norm = Settings._wss_from_host(cand)
            if norm:
                return norm

        # 2) Ankr keyed WSS — the authenticated endpoint.
        if ankr_key:
            return f"wss://rpc.ankr.com/arbitrum/ws/{ankr_key}"

        # 3) Derive wss://<host>/ws from the HTTPS fallback RPC host.
        base = fallback_rpc or ankr_rpc or ""
        if base:
            norm = Settings._wss_from_host(base)
            if norm:
                return norm

        # 4) Last resort: public Arbitrum WS gateway (may 401 without auth).
        return "wss://arb1.arbitrum.io/ws"

    @staticmethod
    def _wss_from_host(value: str) -> str:
        """Normalize any URL/host to wss://<host>/ws, or '' if not parseable."""
        cand = (value or "").strip()
        if not cand:
            return ""
        # Drop any scheme prefixes (handles wss://wss:// and wss://https://).
        while "://" in cand:
            cand = cand.split("://", 1)[1]
        host = cand.split("/")[0]
        if host:
            return f"wss://{host}/ws"
        return ""
