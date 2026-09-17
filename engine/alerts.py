"""Mobile alert delivery for signal-only 714 Method operation.

The iTradeBot public site does not expose a documented API for third-party
order submission.  This module deliberately sends *signals*, not orders.  A
trader can review the alert on a phone and place/confirm the trade in their
own MT4/MT5/iTradeBot setup.

Supported channels are intentionally dependency-free:
- Telegram Bot API (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)
- A generic JSON webhook (ALERT_WEBHOOK_URL)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


class AlertManager:
    """Deliver mobile alerts without ever submitting a trading order."""

    def __init__(self, cfg: dict[str, Any], state):
        self.cfg = cfg.get("alerts", {}) or {}
        self.state = state
        self.enabled = bool(self.cfg.get("enabled", False))
        self.timeout = float(self.cfg.get("timeout_seconds", 8))
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.webhook_url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
        self._warned_no_channel = False

    @property
    def configured_channels(self) -> list[str]:
        channels = []
        if self.telegram_token and self.telegram_chat_id:
            channels.append("telegram")
        if self.webhook_url:
            channels.append("webhook")
        return channels

    def signal_message(self, signal: dict[str, Any]) -> str:
        """Create a short, phone-friendly signal message."""
        side = str(signal.get("side", "")).upper()
        symbol = signal.get("symbol", "?")
        entry = _number(signal.get("entry"))
        stop = _number(signal.get("stop"))
        pattern = signal.get("type", "714 setup")
        structure = signal.get("structure", "unknown")
        bos = signal.get("bos") or "none"
        atr = signal.get("atr")
        atr_text = _number(atr) if atr is not None else "n/a"
        ts = signal.get("ts") or datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        return (
            f"714 SIGNAL — {side} {symbol}\n"
            f"Pattern: {pattern}\n"
            f"Entry reference: {entry}\n"
            f"Pattern stop: {stop}\n"
            f"Structure: {structure} | BoS: {bos}\n"
            f"ATR: {atr_text}\n"
            f"Time: {ts} UTC\n\n"
            "Review spread, news, position size, and the chart before acting. "
            "Signal only — no order was placed."
        )

    def notify_signal(self, signal: dict[str, Any]) -> bool:
        """Send a signal to all configured channels.

        Returns True when at least one channel accepted the message. Delivery
        failures are logged but never stop the market-scanning loop.
        """
        if not self.enabled:
            return False
        channels = self.configured_channels
        if not channels:
            if not self._warned_no_channel:
                self.state.add_log(
                    "alerts enabled but no Telegram credentials or webhook URL configured",
                    "WARN",
                )
                self._warned_no_channel = True
            return False

        message = self.signal_message(signal)
        delivered = False
        if "telegram" in channels:
            try:
                self._send_telegram(message)
                delivered = True
                self.state.add_log(
                    f"mobile alert sent via Telegram: {signal.get('side', '').upper()} "
                    f"{signal.get('symbol', '')}"
                )
            except Exception as exc:  # pragma: no cover - network dependent
                self.state.add_log(f"Telegram alert failed: {exc}", "WARN")

        if "webhook" in channels:
            try:
                self._send_webhook(signal, message)
                delivered = True
                self.state.add_log(
                    f"mobile alert sent via webhook: {signal.get('side', '').upper()} "
                    f"{signal.get('symbol', '')}"
                )
            except Exception as exc:  # pragma: no cover - network dependent
                self.state.add_log(f"webhook alert failed: {exc}", "WARN")

        return delivered

    def _send_telegram(self, message: str) -> None:
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        body = urllib.parse.urlencode(
            {"chat_id": self.telegram_chat_id, "text": message}
        ).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"HTTP {response.status}")

    def _send_webhook(self, signal: dict[str, Any], message: str) -> None:
        payload = {
            "event": "714_signal",
            "message": message,
            "signal": signal,
            "signal_only": True,
        }
        body = json.dumps(payload, default=str).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"HTTP {response.status}")


def _number(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return str(value)
