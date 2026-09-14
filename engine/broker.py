# ============================================================
#  714 METHOD — Alpaca broker wrapper
#  Paper & live both go through the same interface; `mode` decides
#  which endpoint. Supports equities + crypto.
# ============================================================
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest,
        GetOrdersRequest,
        ClosePositionRequest,
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    ALPACA_AVAILABLE = True
except Exception:  # pragma: no cover
    ALPACA_AVAILABLE = False

TIMEFRAME_MAP = {
    "1Min": TimeFrame.Minute,
    "5Min": TimeFrame.Minute,
    "15Min": TimeFrame.Minute,
    "1Hour": TimeFrame.Hour,
    "1Day": TimeFrame.Day,
}


class Broker:
    """Thin wrapper around alpaca-py. Raises RuntimeError if not configured."""

    def __init__(self, cfg, state):
        if not ALPACA_AVAILABLE:
            raise RuntimeError("alpaca-py is not installed")
        key = os.getenv("ALPACA_API_KEY")
        secret = os.getenv("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError(
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY (env or .env file)"
            )
        self.mode = cfg["broker"]["mode"]
        self.trading = TradingClient(key, secret, paper=(self.mode == "paper"))
        self.data = StockHistoricalDataClient(key, secret)
        self.state = state

    # ---- account ----
    def equity(self):
        acct = self.trading.get_account()
        return float(acct.equity)

    # ---- market data ----
    def bars(self, symbol, timeframe="15Min", limit=100):
        """Return a pandas DataFrame of OHLCV bars."""
        tf = TIMEFRAME_MAP.get(timeframe, TimeFrame.Minute)
        unit = 15 if timeframe == "15Min" else (5 if timeframe == "5Min" else 1)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            adjustment="raw",
            limit=limit,
        )
        bars = self.data.get_stock_bars(req).df
        if bars is None or bars.empty:
            return None
        df = bars.reset_index()
        df = df.rename(
            columns={
                "timestamp": "ts",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            }
        )
        df = df[["ts", "open", "high", "low", "close", "volume"]].copy()
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df = df.set_index("ts")
        return df

    def latest_price(self, symbol):
        df = self.bars(symbol, "1Min", 2)
        if df is None or df.empty:
            return None
        return float(df["close"].iloc[-1])

    # ---- orders / positions ----
    def submit_market(self, symbol, side, qty):
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        return self.trading.submit_order(req)

    def submit_limit(self, symbol, side, qty, limit_price):
        from alpaca.trading.requests import LimitOrderRequest
        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            limit_price=round(limit_price, 2),
            time_in_force=TimeInForce.DAY,
        )
        return self.trading.submit_order(req)

    def positions(self):
        return self.trading.get_all_positions()

    def position(self, symbol):
        try:
            return self.trading.get_open_position(symbol)
        except Exception:
            return None

    def close_position(self, symbol):
        self.trading.close_position(symbol, ClosePositionRequest())

    def cancel_open_orders(self, symbol=None):
        orders = self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        for o in orders:
            if symbol is None or o.symbol == symbol:
                self.trading.cancel_order_by_id(o.id)
