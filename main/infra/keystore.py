from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_account.signers.local import LocalAccount

logger = logging.getLogger(__name__)


class Keystore:
    def __init__(self, keystore_path: str, passphrase: str) -> None:
        self.keystore_path = keystore_path
        self.passphrase = passphrase
        self._account: LocalAccount | None = None

    def _load_account(self) -> LocalAccount:
        if self._account is not None:
            return self._account

        path = Path(self.keystore_path)
        if not path.exists():
            raise FileNotFoundError(f"Keystore file not found: {self.keystore_path}")

        keystore_json = json.loads(path.read_text())
        private_key = Account.decrypt(keystore_json, self.passphrase)
        self._account = Account.from_key(private_key)
        return self._account

    @property
    def address(self) -> str:
        return self._load_account().address

    def sign_transaction(self, tx_payload: dict[str, Any]) -> Any:
        account = self._load_account()
        return account.sign_transaction(tx_payload)

    @staticmethod
    def create_keystore(private_key: str, passphrase: str, output_path: str) -> str:
        account = Account.from_key(private_key)
        encrypted = Account.encrypt(account.key, passphrase)
        Path(output_path).write_text(json.dumps(encrypted, indent=2))
        Path(output_path).chmod(0o600)
        logger.info("Keystore created at %s", output_path)
        return output_path
