from __future__ import annotations

MAJOR_ASSET_BLACKLIST: set[str] = {
    "0x82af49447d8a07e3bd95bd0d56f35241523fbab1".lower(),
    "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f".lower(),
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831".lower(),
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9".lower(),
    "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1".lower(),
    "0x912ce59144191c1204e64559fe8253a0e49e6548".lower(),
    "0xf97f4df75117a78c1a5a0dbb814af92458539fb4".lower(),
    "0xfa9fa403952bf6964d4469a7ebbe16ac158aed17".lower(),
}

WETH_ADDRESS = "0x82aF49447D8a07e3bd95bD0d56f35241523fBab1"
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
USDT_ADDRESS = "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9"
WBTC_ADDRESS = "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f"
DAI_ADDRESS = "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1"
ARB_ADDRESS = "0x912CE59144191C1204E64559FE8253a0e49E6548"
LINK_ADDRESS = "0xf97f4df75117a78c1a5a0dbb814af92458539fb4"
UNI_ADDRESS = "0xfa9fa403952bf6964d4469a7ebbe16ac158aed17"

PAIR_CREATED_V2_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
POOL_CREATED_V3_TOPIC = "0x783cca1c0412dd0d695e71b59427b0c48381a901f1565039e6f064699ea65b80"

MIN_LIQUIDITY_USD = 2500.0
MAX_LIQUIDITY_USD = 100000.0
MAX_TOKEN_AGE_HOURS = 72


class FactoryConfig:
    def __init__(
        self,
        name: str,
        factory_address: str,
        event_topic: str,
        dex_venue: str,
        version: str,
    ) -> None:
        self.name = name
        self.factory_address = factory_address.lower()
        self.event_topic = event_topic
        self.dex_venue = dex_venue
        self.version = version


FACTORY_REGISTRY: list[FactoryConfig] = [
    FactoryConfig(
        name="Camelot V2",
        factory_address="0x6EcCab422D763aC031210895C81787E87B43A652",
        event_topic=PAIR_CREATED_V2_TOPIC,
        dex_venue="Camelot_V2",
        version="v2",
    ),
    FactoryConfig(
        name="SushiSwap V2",
        factory_address="0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
        event_topic=PAIR_CREATED_V2_TOPIC,
        dex_venue="SushiSwap_V2",
        version="v2",
    ),
    FactoryConfig(
        name="Uniswap V2",
        factory_address="0xf1d7cc64fb4452f05c498126312ebe29f30fbcf9",
        event_topic=PAIR_CREATED_V2_TOPIC,
        dex_venue="Uniswap_V2",
        version="v2",
    ),
]

FACTORY_BY_ADDRESS: dict[str, FactoryConfig] = {
    f.factory_address: f for f in FACTORY_REGISTRY
}
