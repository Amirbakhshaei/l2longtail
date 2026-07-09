"""
Live execution engine for Arbitrum One.

Handles calldata encoding, transaction signing, and private RPC submission.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3

logger = logging.getLogger(__name__)

SWAP_EXACT_TOKENS_FOR_TOKENS_SELECTOR = "0x38ed1739"
SWAP_EXACT_TOKENS_FOR_ETH_SELECTOR = "0x7ff36ab5"
SWAP_EXACT_ETH_FOR_TOKENS_SELECTOR = "0x7ff36ab5"


@dataclass
class SwapParams:
    token_in: str
    token_out: str
    amount_in: int
    amount_out_min: int
    path: list[str]
    to: str
    deadline: int


@dataclass
class TransactionResult:
    tx_hash: str
    status: Literal["SUBMITTED", "CONFIRMED", "FAILED"]
    block_number: int | None = None
    gas_used: int | None = None
    error: str | None = None


class LiveExecutor:
    def __init__(
        self,
        rpc_url: str,
        private_key: str | None = None,
        dry_run: bool = True,
        bundle_relay_url: str | None = None,
    ) -> None:
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.dry_run = dry_run
        self.bundle_relay_url = bundle_relay_url

        if private_key:
            self.account: LocalAccount | None = Account.from_key(private_key)
        else:
            self.account = None

    def _build_swap_calldata(self, params: SwapParams) -> str:
        selector = SWAP_EXACT_TOKENS_FOR_TOKENS_SELECTOR

        amount_in_hex = format(params.amount_in, "064x")
        amount_out_min_hex = format(params.amount_out_min, "064x")
        path_offset = "0000000000000000000000000000000000000000000000000000000000000080"
        path_length = format(len(params.path), "064x")
        path_encoded = ""
        for addr in params.path:
            path_encoded += addr.lower().replace("0x", "").zfill(64)

        to_padded = params.to.lower().replace("0x", "").zfill(64)
        deadline_hex = format(params.deadline, "064x")

        calldata = (
            selector
            + amount_in_hex
            + amount_out_min_hex
            + path_offset
            + path_length
            + path_encoded
            + to_padded
            + deadline_hex
        )

        return calldata

    def _build_swap_exact_eth_calldata(
        self, params: SwapParams
    ) -> str:
        selector = "0x7ff36ab5"

        amount_out_min_hex = format(params.amount_out_min, "064x")
        path_offset = "0000000000000000000000000000000000000000000000000000000000000060"
        path_length = format(len(params.path), "064x")
        path_encoded = ""
        for addr in params.path:
            path_encoded += addr.lower().replace("0x", "").zfill(64)

        to_padded = params.to.lower().replace("0x", "").zfill(64)
        deadline_hex = format(params.deadline, "064x")

        calldata = (
            selector
            + amount_out_min_hex
            + path_offset
            + path_length
            + path_encoded
            + to_padded
            + deadline_hex
        )

        return calldata

    async def execute_swap(
        self,
        router_address: str,
        params: SwapParams,
        value_eth: float = 0.0,
    ) -> TransactionResult:
        if self.dry_run:
            return await self._dry_run_swap(router_address, params, value_eth)

        if not self.account:
            return TransactionResult(
                tx_hash="",
                status="FAILED",
                error="No private key configured",
            )

        try:
            calldata = self._build_swap_calldata(params)

            nonce = self.w3.eth.get_transaction_count(
                self.account.address
            )

            gas_price = self.w3.eth.gas_price
            max_fee = int(gas_price * 1.5)
            max_priority = self.w3.eth.max_priority_fee

            tx = {
                "from": self.account.address,
                "to": Web3.to_checksum_address(router_address),
                "value": Web3.to_wei(value_eth, "ether"),
                "gas": 300000,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": max_priority,
                "nonce": nonce,
                "chainId": 42161,
                "data": bytes.fromhex(calldata[2:]),
                "type": 2,
            }

            signed_tx = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(
                signed_tx.raw_transaction
            )

            logger.info("TX submitted: %s", tx_hash.hex())

            return TransactionResult(
                tx_hash=tx_hash.hex(),
                status="SUBMITTED",
            )

        except Exception as e:
            logger.error("Swap execution failed: %s", e)
            return TransactionResult(
                tx_hash="",
                status="FAILED",
                error=str(e),
            )

    async def _dry_run_swap(
        self,
        router_address: str,
        params: SwapParams,
        value_eth: float,
    ) -> TransactionResult:
        logger.info(
            "DRY RUN: swap %d -> %s via %s",
            params.amount_in,
            params.token_out[:10],
            router_address[:10],
        )

        return TransactionResult(
            tx_hash=f"0x{'0' * 64}",
            status="SUBMITTED",
        )

    async def send_private_transaction(
        self,
        signed_tx_bytes: bytes,
    ) -> TransactionResult:
        if not self.bundle_relay_url:
            logger.warning("No bundle relay URL, using public mempool")
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx_bytes)
            return TransactionResult(
                tx_hash=tx_hash.hex(),
                status="SUBMITTED",
            )

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.bundle_relay_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_sendBundle",
                        "params": [
                            {
                                "txs": [signed_tx_bytes.hex()],
                                "blockNumber": hex(
                                    self.w3.eth.block_number + 1
                                ),
                            }
                        ],
                    },
                )
                response.raise_for_status()
                data = response.json()

                if "result" in data:
                    return TransactionResult(
                        tx_hash="pending_bundle",
                        status="SUBMITTED",
                    )
                else:
                    return TransactionResult(
                        tx_hash="",
                        status="FAILED",
                        error=str(data.get("error", "Unknown error")),
                    )

        except Exception as e:
            logger.error("Private transaction failed: %s", e)
            return TransactionResult(
                tx_hash="",
                status="FAILED",
                error=str(e),
            )

    def get_nonce(self) -> int:
        if not self.account:
            return 0
        return self.w3.eth.get_transaction_count(self.account.address)

    def estimate_gas(
        self,
        from_address: str,
        to_address: str,
        data: bytes,
        value: int = 0,
    ) -> int:
        try:
            return self.w3.eth.estimate_gas({
                "from": Web3.to_checksum_address(from_address),
                "to": Web3.to_checksum_address(to_address),
                "data": data,
                "value": value,
            })
        except Exception as e:
            logger.debug("Gas estimation failed: %s", e)
            return 300000
