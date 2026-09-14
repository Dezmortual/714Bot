# ============================================================
#  714 METHOD — mock broker (synthetic data + simulated fills)
#  Lets the bot run end-to-end with NO API keys: it maintains a
#  persistent, advancing price series (with embedded W/M patterns)
#  so signals fire over time, and simulates fills / PnL.
# ============================================================
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from collections import deque


# Normalized -1..1 cycle with clean double-tops (M) and double-bottoms (W)
# with DEEP retracements so structure detection fires reliably:
#   rally -> pullback -> retest top (M) -> selloff -> bounce -> retest
#   bottom (W) -> rally ...
CYCLE = [0.0, 0.35, 0.7, 1.0, 0.55, 0.98, 0.5,
         0.0, -0.4, -0.75, -1.0, -0.55, -0.98, -0.5]


class MockBroker:
    """Synthetic broker with a persistent, advancing price series."""

    def __init__(self, cfg, state):
        self.mode = "simulation"
        self.state = state
        self._equity = 10_000.0
        self._positions = {}   # symbol -> dict(entry, qty, side)
        self._bars = {}        # symbol -> deque of (ts, o, h, l, c, v)
        self._seed = {}        # symbol -> rng
        self._next_ts = {}     # symbol -> next bar timestamp

    def _rng(self, symbol):
        if symbol not in self._seed:
            self._seed[symbol] = np.random.default_rng(sum(ord(c) for c in symbol))
        return self._seed[symbol]

    def _start(self, symbol):
        # deterministic base price per symbol (loosely priced like equities)
        return 50.0 + (sum(ord(c) for c in symbol) % 300)

    def _init(self, symbol):
        if symbol not in self._bars:
            self._bars[symbol] = deque(maxlen=2000)
            self._next_ts[symbol] = datetime.now(timezone.utc).replace(
                second=0, microsecond=0) - timedelta(minutes=15)

    def _append_bar(self, symbol):
        """Generate and append one new bar for the symbol."""
        self._init(symbol)
        rng = self._rng(symbol)
        base = self._start(symbol)
        amp = 1.5
        noise = 0.05
        step = len(self._bars[symbol])
        ts = self._next_ts[symbol]
        shape = CYCLE[step % len(CYCLE)]
        close = base + amp * shape + rng.normal(0, noise)
        o = close + rng.normal(0, noise)
        h = max(o, close) + abs(rng.normal(0, noise))
        l = min(o, close) - abs(rng.normal(0, noise))
        v = rng.integers(1000, 10000)
        self._bars[symbol].append((ts, float(o), float(h), float(l), float(close), int(v)))
        self._next_ts[symbol] = ts + timedelta(minutes=15)

    def equity(self):
        pnl = 0.0
        for sym, p in self._positions.items():
            px = self.latest_price(sym) or p["entry"]
            if p["side"] == "buy":
                pnl += (px - p["entry"]) * p["qty"]
            else:
                pnl += (p["entry"] - px) * p["qty"]
        return self._equity + pnl

    def available_cash(self):
        # mock: equity minus notional committed to open positions
        used = 0.0
        for sym, p in self._positions.items():
            used += p["entry"] * p["qty"]
        return max(self._equity - used, 0.0)

    def bars(self, symbol, timeframe="15Min", limit=100):
        """Return a DataFrame of the most recent `limit` bars, appending a
        fresh bar each call so price 'moves' forward over time."""
        self._init(symbol)
        while len(self._bars[symbol]) < limit:
            self._append_bar(symbol)
        self._append_bar(symbol)  # advance one bar per call (live ticking)
        bars = list(self._bars[symbol])[-limit:]
        df = pd.DataFrame(
            {"open": [b[1] for b in bars], "high": [b[2] for b in bars],
             "low": [b[3] for b in bars], "close": [b[4] for b in bars],
             "volume": [b[5] for b in bars]},
            index=[b[0] for b in bars],
        )
        return df

    def latest_price(self, symbol):
        if symbol not in self._bars or not self._bars[symbol]:
            return None
        return self._bars[symbol][-1][4]

    # ---- order simulation ----
    def submit_market(self, symbol, side, qty):
        px = self.latest_price(symbol)
        self._positions[symbol] = {"entry": px, "qty": qty, "side": side}
        self.state.add_log(f"[SIM] filled {side} {qty:.4f} {symbol} @ {px:.4f}")
        return {"id": f"sim-{len(self._positions)}", "symbol": symbol}

    def positions(self):
        return list(self._positions.values())

    def position(self, symbol):
        return self._positions.get(symbol)

    def close_position(self, symbol):
        p = self._positions.pop(symbol, None)
        if p:
            px = self.latest_price(symbol) or p["entry"]
            pnl = (px - p["entry"]) * p["qty"] if p["side"] == "buy" else (p["entry"] - px) * p["qty"]
            self._equity += pnl
            self.state.record_trade(p["side"], p["qty"], p["entry"], px, pnl, "close")
            self.state.add_log(f"[SIM] closed {symbol} pnl={pnl:.2f}")

    def reduce_position(self, symbol, fraction):
        p = self._positions.get(symbol)
        if not p:
            return
        px = self.latest_price(symbol) or p["entry"]
        close_qty = p["qty"] * fraction
        pnl = (px - p["entry"]) * close_qty if p["side"] == "buy" else (p["entry"] - px) * close_qty
        p["qty"] -= close_qty
        self._equity += pnl
        self.state.record_trade(p["side"], close_qty, p["entry"], px, pnl, "partial")
        if p["qty"] <= 1e-9:
            self._positions.pop(symbol, None)

    def cancel_open_orders(self, symbol=None):
        pass
