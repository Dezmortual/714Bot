"""
Multi-timeframe trend dashboard — port of the indicator's MTF panel.

For each timeframe the Pine script compares EMA(20) against EMA(50) and
shows Bullish/Bearish in a table.  Here the same information is logged,
and it can optionally be used as a trade filter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import ccxt  # type: ignore
import pandas as pd

from .datafeed import TIMEFRAME_MS, pine_timeframe
from .strategy import ema


@dataclass
class MTFRow:
    timeframe: str
    bullish: bool
    ema_fast: float
    ema_slow: float
    close: float


class MTFDashboard:
    DEFAULT_TFS = ["15m", "30m", "1h", "4h", "1d"]   # the Pine defaults 15/30/60/240/1D

    def __init__(self, exchange: ccxt.Exchange, symbol: str,
                 timeframes: List[str] | None = None):
        self.exchange = exchange
        self.symbol = symbol
        tfs = timeframes or self.DEFAULT_TFS
        self.timeframes = [pine_timeframe(tf) for tf in tfs]

    def _trend(self, timeframe: str) -> MTFRow:
        limit = 120
        rows = self.exchange.fetch_ohlcv(
            self.symbol, timeframe=timeframe, limit=limit + 5
        )
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        df = df.set_index("time").sort_index().astype(float)
        # drop the still-forming candle (Pine uses confirmed bars)
        tf_ms = TIMEFRAME_MS[timeframe]
        if int(df.index[-1].timestamp() * 1000) + tf_ms > self.exchange.milliseconds():
            df = df.iloc[:-1]
        close = df["close"]
        fast = float(ema(close, 20).iloc[-1])
        slow = float(ema(close, 50).iloc[-1])
        return MTFRow(
            timeframe=timeframe,
            bullish=fast > slow,
            ema_fast=fast,
            ema_slow=slow,
            close=float(close.iloc[-1]),
        )

    def snapshot(self) -> List[MTFRow]:
        rows: List[MTFRow] = []
        for tf in self.timeframes:
            try:
                rows.append(self._trend(tf))
            except Exception:  # keep the dashboard resilient
                rows.append(MTFRow(tf, False, float("nan"), float("nan"), float("nan")))
        return rows

    def render(self, rows: List[MTFRow] | None = None) -> str:
        rows = rows if rows is not None else self.snapshot()
        lines = ["+------+----------+", "|  TF  |  Trend   |", "+------+----------+"]
        for r in rows:
            lines.append(f"| {r.timeframe:>4} | {'Bullish' if r.bullish else 'Bearish':^8} |")
        lines.append("+------+----------+")
        return "\n".join(lines)

    def agrees_with(self, direction: int, rows: List[MTFRow],
                    min_agreeing: int = 3) -> bool:
        """Optional filter: at least ``min_agreeing`` timeframes must point the
        same way as the signal (+1 long / -1 short)."""
        agree = sum(1 for r in rows if r.bullish == (direction == 1))
        return agree >= min_agreeing
