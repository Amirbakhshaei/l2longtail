import asyncio
import json
import logging
from pathlib import Path

from config.constants import BLACKLIST_SEED_FILE
from config.settings import Settings
from db.blacklist import BlacklistDB
from db.cache import ContractCache

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = Settings()
    cache = ContractCache(settings.db_path)
    await cache.init()

    blacklist_db = BlacklistDB(settings.db_path)

    seed_path = Path(BLACKLIST_SEED_FILE)
    if not seed_path.exists():
        logger.error("Blacklist seed file not found: %s", seed_path)
        return

    entries = json.loads(seed_path.read_text())
    count = await blacklist_db.seed_from_list(entries)
    logger.info("Seeded %d blacklist entries from %s", count, seed_path)

    all_entries = await blacklist_db.list_all()
    for entry in all_entries:
        logger.info("  blacklisted: %s (%s)", entry["token_address"], entry["reason"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
