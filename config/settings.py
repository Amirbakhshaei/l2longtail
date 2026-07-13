from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    dry_run: bool = Field(default=True, description="True = simulate, False = sign and broadcast")

    ankr_api_key: str = ""
    ankr_rpc_url: str = ""
    fallback_rpc_url: str = "https://arb1.arbitrum.io/rpc"
    flashbots_rpc_url: str = "https://rpc.flashbots.net/fast"

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
