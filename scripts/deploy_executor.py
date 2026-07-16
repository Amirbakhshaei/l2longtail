"""
Deploy the FlashloanExecutor contract to Arbitrum One.

Compiles contracts/FlashloanExecutor.sol with solcx, deploys it via the
encrypted keystore (config.settings.keystore_path / keystore_passphrase) and
the configured RPC, then writes the deployed address to FLASHLOAN_EXECUTOR_ADDRESS
in .env so the engine can run live.

Safety:
  * Refuses to deploy unless DRY_RUN is explicitly false.
  * Requires a funded keystore + RPC URL.
  * Never overwrites an existing executor address unless --force.

Usage:
    python scripts/deploy_executor.py            # compiles + deploys (needs DRY_RUN=false)
    python scripts/deploy_executor.py --compile-only   # just verify it compiles
    python scripts/deploy_executor.py --force    # overwrite existing address in .env
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings  # noqa: E402
from infra.keystore import Keystore  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("deploy_executor")

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "FlashloanExecutor.sol"
SOLC_VERSION = "0.8.20"


def compile_contract() -> tuple[list, str]:
    """Return (abi, bytecode) for FlashloanExecutor, compiling with solcx."""
    import solcx

    if not solcx.get_installed_solc_versions():
        solcx.install_solc(SOLC_VERSION)
    source = CONTRACT_PATH.read_text()
    compiled = solcx.compile_source(
        source, output_values=["abi", "bin"], solc_version=SOLC_VERSION
    )
    name = next(k for k in compiled if k.endswith(":FlashloanExecutor"))
    artifact = compiled[name]
    abi = artifact["abi"]
    bin = artifact["bin"]
    if not bin or bin == "0x":
        raise RuntimeError("Compilation produced empty bytecode")
    logger.info("Compiled FlashloanExecutor: %d ABI entries, %d bin bytes",
                len(abi), len(bin) // 2)
    return abi, bin


async def deploy(abi: list, bytecode: str, settings: Settings) -> str:
    from web3 import Web3
    from eth_account import Account  # noqa: F401 (keystore returns LocalAccount)

    rpc_url = settings.ankr_rpc_url or settings.fallback_rpc_url
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to RPC: {rpc_url}")

    keystore = Keystore(settings.keystore_path, settings.keystore_passphrase)
    owner = keystore.address
    logger.info("Deployer: %s", owner)

    nonce = w3.eth.get_transaction_count(owner)
    gas_price = w3.eth.gas_price

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    # constructor(address vault_, address weth_)
    vault = Web3.to_checksum_address(settings.balancer_vault_address)
    weth = Web3.to_checksum_address(
        "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    )
    tx = contract.constructor(vault, weth).build_transaction(
        {
            "from": owner,
            "nonce": nonce,
            "gas": 3_000_000,
            "gasPrice": gas_price,
            "chainId": 42161,
        }
    )

    signed = keystore.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    logger.info("Deploy tx sent: %s", tx_hash.hex())

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt["status"] != 1:
        raise RuntimeError(f"Deploy tx reverted: {tx_hash.hex()}")

    address = receipt["contractAddress"]
    logger.info("FlashloanExecutor deployed at: %s", address)
    return Web3.to_checksum_address(address)


def write_executor_address(address: str, settings: Settings, force: bool) -> None:
    env_path = Path(".env")
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    key = "FLASHLOAN_EXECUTOR_ADDRESS"
    existing = next((ln for ln in lines if ln.startswith(f"{key}=")), None)

    if existing and not force:
        logger.warning(
            "Refusing to overwrite existing %s=%s (use --force)", key, existing
        )
        return

    if existing:
        lines = [ln for ln in lines if not ln.startswith(f"{key}=")]
    lines.append(f"{key}={address}")
    env_path.write_text("\n".join(lines) + "\n")
    logger.info("Wrote %s=%s to .env", key, address)


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Deploy FlashloanExecutor")
    parser.add_argument("--compile-only", action="store_true",
                        help="Only compile; do not deploy.")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing FLASHLOAN_EXECUTOR_ADDRESS in .env.")
    args = parser.parse_args()

    settings = Settings()
    abi, bytecode = compile_contract()

    if args.compile_only:
        logger.info("Compile-only mode: contract is valid. Exiting.")
        return

    if settings.dry_run:
        logger.error("Refusing to deploy while DRY_RUN=true. "
                     "Set DRY_RUN=false in .env and re-run.")
        sys.exit(2)

    if not settings.keystore_passphrase or not Path(settings.keystore_path).exists():
        logger.error("Keystore missing (path=%s). Cannot deploy.",
                     settings.keystore_path)
        sys.exit(3)

    address = await deploy(abi, bytecode, settings)
    write_executor_address(address, settings, args.force)
    logger.info("Done. Engine can now run live with executor_address set.")


if __name__ == "__main__":
    asyncio.run(main())
