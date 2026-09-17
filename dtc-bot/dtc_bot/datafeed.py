"""
Exchange connectivity for market data (ccxt).

Only public endpoints are needed for backtesting and paper trading, so the
bot works without API keys until you switch to live mode.
"""
from __future__ import annotations

from typing import Optional

import ccxt  # type: ignore
import pandas as pd

TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}

# Pine input.timeframe() values -> ccxt timeframes (the indicator's defaults)
PINE_TF_MAP = {
    "15": "15m", "30": "30m", "60": "1h", "240": "4h", "1D": "1d",
}


def pine_timeframe(tf: str) -> str:
    return PINE_TF_MAP.get(str(tf), str(tf))


def make_exchange(name: str, api_key: str = "", secret: str = "",
                  password: str = "") -> ccxt.Exchange:
    """Create a configured ccxt exchange instance."""
    if not hasattr(ccxt, name):
        raise ValueError(f"Unknown ccxt exchange id: {name!r}")
    klass = getattr(ccxt, name)
    params = {"enableRateLimit": True}
    if api_key:
        params["apiKey"] = api_key
    if secret:
        params["secret"] = secret
    if password:
        params["password"] = password
    return klass(params)


class DataFeed:
    """Fetch OHLCV candles for one symbol/timeframe from an exchange."""

    OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

    def __init__(self, exchange: ccxt.Exchange, symbol: str, timeframe: str):
        if timeframe not in TIMEFRAME_MS:
            raise ValueError(f"Unsupported timeframe {timeframe!r}")
        self.exchange = exchange
        self.symbol = symbol
        self.timeframe = timeframe
        self.tf_ms = TIMEFRAME_MS[timeframe]

    # -- helpers -------------------------------------------------------
    def _to_df(self, rows: list) -> pd.DataFrame:
        df = pd.DataFrame(rows, columns=self.OHLCV_COLUMNS)
        if df.empty:
            return df
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        df = df.set_index("time").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df.astype(float)

    def drop_forming_candle(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove the last row if it is the still-forming (unclosed) candle —
        the equivalent of Pine's ``barstate.isconfirmed``."""
        if df.empty:
            return df
        last_open_ms = int(df.index[-1].timestamp() * 1000)
        now_ms = self.exchange.milliseconds()
        if last_open_ms + self.tf_ms > now_ms:
            df = df.iloc[:-1]
        return df

    # -- public API ----------------------------------------------------
    def fetch_closed(self, limit: int = 600) -> pd.DataFrame:
        """Return the most recent ``limit`` CLOSED candles."""
        df = self._to_df(
            self.exchange.fetch_ohlcv(
                self.symbol, timeframe=self.timeframe, limit=min(limit + 5, 1000)
            )
        )
        df = self.drop_forming_candle(df)
        return df.tail(limit)

    def fetch_history(self, days: int = 90, max_candles: int = 50_000) -> pd.DataFrame:
        """Paginate backwards to fetch ``days`` of closed candles."""
        since_ms = self.exchange.milliseconds() - days * 86_400_000
        all_rows: list = []
        pages = 0
        while len(all_rows) < max_candles and pages < 500:
            batch = self.exchange.fetch_ohlcv(
                self.symbol, timeframe=self.timeframe, since=since_ms, limit=1000
            )
            if not batch:
                break
            if all_rows and batch[-1][0] <= all_rows[-1][0]:
                break  # no forward progress
            all_rows.extend(batch)
            since_ms = batch[-1][0] + self.tf_ms
            pages += 1
            if len(batch) < 2:
                break
            now_ms = self.exchange.milliseconds()
            if batch[-1][0] + self.tf_ms >= now_ms:
                break
        df = self._to_df(all_rows)
        return self.drop_forming_candle(df)

    def last_price(self) -> float:
        try:
            ticker = self.exchange.fetch_ticker(self.symbol)
            if ticker and ticker.get("last"):
                return float(ticker["last"])
        except Exception:
            pass
        df = self.fetch_closed(2)
        return float(df["close"].iloc[-1])
