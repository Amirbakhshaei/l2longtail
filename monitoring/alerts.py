from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)


class TelegramAlerts:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)
        self._last_summary_at: float = 0.0
        self.summary_interval: float = 1800.0

    async def send_telegram(self, message: str) -> None:
        if not self._enabled:
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
        except Exception as e:
            logger.error("Telegram alert failed: %s", e)

    # ---- Formatted notification methods ----

    async def notify_engine_start(self, mode: str, trade_size: float, min_spread: float) -> None:
        msg = (
            f"<b>Engine Started</b>\n"
            f"Mode: <code>{mode}</code>\n"
            f"Trade size: <code>${trade_size:.0f}</code>\n"
            f"Min spread: <code>{min_spread:.1f}%</code>\n"
            f"Time: <code>{time.strftime('%Y-%m-%d %H:%M:%S')}</code>"
        )
        await self.send_telegram(msg)

    async def notify_engine_stop(
        self, uptime_secs: float, executed: int, aborted: int, profit: float
    ) -> None:
        h, rem = divmod(int(uptime_secs), 3600)
        m, s = divmod(rem, 60)
        msg = (
            f"<b>Engine Stopped</b>\n"
            f"Uptime: <code>{h:02d}:{m:02d}:{s:02d}</code>\n"
            f"Trades executed: <code>{executed}</code>\n"
            f"Trades aborted: <code>{aborted}</code>\n"
            f"Total net profit: <code>${profit:.4f}</code>"
        )
        await self.send_telegram(msg)

    async def notify_token_cleared(self, token_address: str, dex_count: int) -> None:
        msg = (
            f"<b>Token Cleared</b>\n"
            f"Address: <code>{token_address}</code>\n"
            f"DEXes: <code>{dex_count}</code>"
        )
        await self.send_telegram(msg)

    async def notify_trade_executed(
        self, token_address: str, buy_dex: str, sell_dex: str,
        spread_pct: float, net_profit: float, mode: str,
    ) -> None:
        emoji = " LIVE" if mode == "LIVE" else " PAPER"
        msg = (
            f"<b>Trade Executed{emoji}</b>\n"
            f"Token: <code>{token_address}</code>\n"
            f"Buy: <code>{buy_dex}</code> | Sell: <code>{sell_dex}</code>\n"
            f"Spread: <code>{spread_pct:.2f}%</code>\n"
            f"Net profit: <code>${net_profit:.4f}</code>"
        )
        await self.send_telegram(msg)

    async def notify_trade_aborted(
        self, token_address: str, spread_pct: float, reason: str
    ) -> None:
        msg = (
            f"<b>Trade Aborted</b>\n"
            f"Token: <code>{token_address}</code>\n"
            f"Spread: <code>{spread_pct:.2f}%</code>\n"
            f"Reason: <code>{reason}</code>"
        )
        await self.send_telegram(msg)

    async def notify_error(self, source: str, error: str) -> None:
        msg = (
            f"<b>Error</b>\n"
            f"Source: <code>{source}</code>\n"
            f"Details: <code>{error[:200]}</code>"
        )
        await self.send_telegram(msg)

    async def notify_opportunity(
        self, token_address: str, dex: str, spread_pct: float,
        net_profit: float, trade_size: float, stage: str,
    ) -> None:
        msg = (
            f"<b>Opportunity Detected [{stage}]</b>\n"
            f"Token: <code>{token_address}</code>\n"
            f"DEX: <code>{dex}</code>\n"
            f"Spread: <code>{spread_pct:.2f}%</code>\n"
            f"Net profit: <code>${net_profit:.4f}</code>\n"
            f"Trade size: <code>${trade_size:.0f}</code>\n"
            f"Time: <code>{time.strftime('%H:%M:%S')}</code>"
        )
        await self.send_telegram(msg)

    async def notify_sync_event(
        self, pair_address: str, dex: str, reserve0: int, reserve1: int
    ) -> None:
        msg = (
            f"<b>Sync Event</b>\n"
            f"Pool: <code>{pair_address}</code>\n"
            f"DEX: <code>{dex}</code>\n"
            f"Reserves: <code>r0={reserve0}</code> <code>r1={reserve1}</code>"
        )
        await self.send_telegram(msg)

    async def notify_status_summary(
        self, cleared_count: int, weth_price: float,
        executed: int, aborted: int, profit: float,
    ) -> None:
        now = time.time()
        if now - self._last_summary_at < self.summary_interval:
            return
        self._last_summary_at = now

        msg = (
            f"<b>Status Summary</b>\n"
            f"WETH: <code>${weth_price:,.2f}</code>\n"
            f"Cleared tokens: <code>{cleared_count}</code>\n"
            f"Executed: <code>{executed}</code> | Aborted: <code>{aborted}</code>\n"
            f"Net profit: <code>${profit:.4f}</code>\n"
            f"Time: <code>{time.strftime('%H:%M:%S')}</code>"
        )
        await self.send_telegram(msg)
