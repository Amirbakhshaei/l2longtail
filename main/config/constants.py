ARBITRUM_CHAIN_ID = 42161

MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

WETH_ADDRESS = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
USDT_ADDRESS = "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9"

DEX_ROUTERS = {
    "uniswap_v2": "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
    "sushiswap": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
    "camelot_v2": "0xc873fEcbd354f5A56E00E710B90EF1836D620000",
    "trader_joe": "0x7BFdb40e7c1B2A47aF4E7008bC2b1a2b5D7F0b7c",
}

UNISWAP_V2_FACTORY = "0xf1d7cc64fb4452f05c498126312ebe29f30fbcf9"
SUSHISWAP_FACTORY = "0xc35DADB65012eC5796536bD9864eD8773aBc74C4"
CAMELOT_V2_FACTORY = "0x6EcCab422D763aC031210895C81787E87B43A652"
TRADER_JOE_FACTORY = "0xaE3e9C2103BDEd932f13C206aC7be48780F31312"

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
]

UNISWAP_V2_PAIR_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint112"},
            {"name": "_reserve1", "type": "uint112"},
            {"name": "_blockTimestampLast", "type": "uint32"},
        ],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
    },
]

BLACKLIST_SEED_FILE = "config/blacklist.json"
