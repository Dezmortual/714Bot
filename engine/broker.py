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
    from alpaca.data.historical import (
        StockHistoricalDataClient,
        CryptoHistoricalDataClient,
    )
    from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
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
        # --- self-diagnose key/mode mismatch ---
        key_prefix = (key or "")[:2].upper()
        if key_prefix == "PK" and self.mode != "paper":
            state.add_log(
                f"WARNING: you set mode={self.mode} but your key starts with 'PK' "
                "(a PAPER key). Paper keys only work in paper mode.", "WARN")
        if key_prefix == "AK" and self.mode != "live":
            state.add_log(
                f"WARNING: you set mode={self.mode} but your key starts with 'AK' "
                "(a LIVE key). Live keys only work in live mode.", "WARN")
        if key_prefix not in ("PK", "AK"):
            state.add_log(
                f"WARNING: your API key starts with '{key_prefix}' — expected 'PK' "
                "(paper) or 'AK' (live). Double-check you pasted the Key ID "
                "correctly.", "WARN")
        if len((secret or "").strip()) < 20:
            state.add_log(
                "WARNING: your secret key looks too short — it may be truncated. "
                "Re-copy the full secret key.", "WARN")
        self.trading = TradingClient(key, secret, paper=(self.mode == "paper"))
        self.data = StockHistoricalDataClient(key, secret)
        self.crypto_data = CryptoHistoricalDataClient(key, secret)
        self.state = state

    def is_crypto(self, symbol):
        return "/" in (symbol or "")

    # ---- account ----
    def equity(self):
        try:
            acct = self.trading.get_account()
            return float(acct.equity)
        except Exception as e:
            msg = str(e)
            if "401" in msg or "not authorized" in msg:
                raise RuntimeError(
                    "Alpaca rejected your credentials (401). Check: (1) key starts "
                    "with PK for paper / AK for live, (2) the secret key is pasted "
                    "in full with no spaces, (3) key+secret are a matching pair "
                    "from the same generation."
                ) from e
            raise

    # ---- market data ----
    def bars(self, symbol, timeframe="15Min", limit=100):
        """Return a pandas DataFrame of OHLCV bars (stocks or crypto)."""
        tf = TIMEFRAME_MAP.get(timeframe, TimeFrame.Minute)
        if self.is_crypto(symbol):
            req = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                limit=limit,
            )
            bars = self.crypto_data.get_crypto_bars(req).df
        else:
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
        # crypto bars carry a 'symbol' column; drop non-OHLCV columns
        keep = ["open", "high", "low", "close", "volume"]
        if "timestamp" in df.columns:
            keep = ["timestamp"] + keep
        df = df[[c for c in keep if c in df.columns]].copy()
        if "timestamp" in df.columns:
            df = df.rename(columns={"timestamp": "ts"})
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
        # Crypto does not support TimeInForce.DAY — must use GTC.
        tif = TimeInForce.GTC if self.is_crypto(symbol) else TimeInForce.DAY
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=tif,
        )
        return self.trading.submit_order(req)

    def submit_limit(self, symbol, side, qty, limit_price):
        from alpaca.trading.requests import LimitOrderRequest
        tif = TimeInForce.GTC if self.is_crypto(symbol) else TimeInForce.DAY
        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            limit_price=round(limit_price, 2),
            time_in_force=tif,
        )
        return self.trading.submit_order(req)

    def positions(self):
        return self.trading.get_all_positions()

    def position(self, symbol):
        try:
            return self.trading.get_open_position(symbol)
        except Exception:
            return None

    def close_position(self, symbol, percentage=None):
        req = ClosePositionRequest(
            percentage=(percentage if percentage is not None else "100")
        )
        try:
            self.trading.close_position(symbol, req)
            return True
        except Exception as e:
            msg = str(e)
            # Position already gone (closed elsewhere / restart) — not an error.
            if "404" in msg or "Not Found" in msg or "not found" in msg.lower():
                self.state.add_log(f"[broker] close {symbol}: position already closed", "INFO")
                return False
            raise

    def reduce_position(self, symbol, fraction):
        """Close a fraction (0.0-1.0) of the position (partial profit)."""
        pct = str(max(1, int(round(fraction * 100))))
        return self.close_position(symbol, percentage=pct)

    def open_positions(self):
        """Return a normalized list of currently-open Alpaca positions:
        [{symbol, side, qty, entry}] so the engine can reconcile after a
        restart instead of re-entering and duplicating positions."""
        out = []
        try:
            for p in self.trading.get_all_positions():
                side = "buy" if str(getattr(p, "side", "long")).lower() == "long" else "sell"
                out.append({
                    "symbol": p.symbol,
                    "side": side,
                    "qty": float(p.qty),
                    "entry": float(p.avg_entry_price),
                })
        except Exception as e:
            self.state.add_log(f"[broker] failed to list positions: {e}", "WARN")
        return out

    def cancel_open_orders(self, symbol=None):
        orders = self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        for o in orders:
            if symbol is None or o.symbol == symbol:
                self.trading.cancel_order_by_id(o.id)
