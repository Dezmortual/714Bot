"""
Optional notifications (Telegram and/or Discord webhook).

Everything here is fail-soft: a notification error is logged and never
interrupts trading.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("dtc_bot.notify")


class Notifier:
    def __init__(self, telegram_bot_token: str = "", telegram_chat_id: str = "",
                 discord_webhook_url: str = "", timeout: int = 10):
        self.tg_token = telegram_bot_token or ""
        self.tg_chat = telegram_chat_id or ""
        self.discord = discord_webhook_url or ""
        self.timeout = timeout

    @classmethod
    def from_config(cls, cfg: dict) -> "Notifier":
        cfg = cfg or {}
        return cls(
            telegram_bot_token=(
                cfg.get("telegram_bot_token")
                or os.environ.get("TELEGRAM_BOT_TOKEN", "")
            ),
            telegram_chat_id=(
                cfg.get("telegram_chat_id")
                or os.environ.get("TELEGRAM_CHAT_ID", "")
            ),
            discord_webhook_url=(
                cfg.get("discord_webhook_url")
                or os.environ.get("DISCORD_WEBHOOK_URL", "")
            ),
        )

    @property
    def enabled(self) -> bool:
        return bool((self.tg_token and self.tg_chat) or self.discord)

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        if self.tg_token and self.tg_chat:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                    json={"chat_id": self.tg_chat, "text": text},
                    timeout=self.timeout,
                )
            except Exception as exc:
                log.warning("Telegram notification failed: %s", exc)
        if self.discord:
            try:
                requests.post(self.discord, json={"content": text},
                              timeout=self.timeout)
            except Exception as exc:
                log.warning("Discord notification failed: %s", exc)
