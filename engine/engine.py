# ============================================================
#  714 METHOD — trading engine (the main loop)
#  Orchestrates: session gating -> data -> signals -> entries ->
#  trade management (breakeven / partial / target) -> exit logging.
# ============================================================
import os
import time
import threading
import traceback
from datetime import datetime, timezone

import pandas as pd
import pytz

from state import STATE
from engine.strategy import generate_signal, atr
from engine.risk import position_size, pip_distance
from engine.mock_broker import MockBroker


def _make_broker(cfg):
    """Return a real Alpaca broker if keys are present, else a mock."""
    if os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"):
        try:
            from engine.broker import Broker
            return Broker(cfg, STATE)
        except Exception as e:
            STATE.add_log(f"Alpaca init failed, falling back to simulation: {e}", "WARN")
    STATE.add_log("No API keys found — running in SIMULATION mode (mock data)", "WARN")
    return MockBroker(cfg, STATE)


def in_session(cfg, now_utc):
    """Return True if the current time falls in a configured session window
    (in the strategy's timezone). Always True if filter disabled."""
    if not cfg["strategy"].get("use_session_filter", True):
        return True
    tz = pytz.timezone(cfg["strategy"].get("session_timezone", "Africa/Johannesburg"))
    local = now_utc.astimezone(tz)
    hhmm = local.strftime("%H:%M")
    for w in cfg["strategy"].get("session_windows", []):
        a, b = w.split("-")
        if a <= hhmm <= b:
            return True
    return False


class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.broker = _make_broker(cfg)
        self._stop = threading.Event()
        # per-symbol management state:
        # {symbol: {side, entry, qty, stop, target, breakeven_done, partial_done, lock}}
        self._mgmt = {}
        self._syms = cfg["strategy"]["symbols"]

    # ---------------------------------------------------------
    def _log(self, msg, level="INFO"):
        STATE.add_log(msg, level)

    def _pip_value(self, symbol, price):
        """Pip size for a symbol. Stocks use the configured absolute pip
        (default $0.01); crypto uses a relative pip (~0.05% of price) so
        targets/breakeven scale sensibly for BTC/ETH/SOL etc."""
        base = self.cfg["risk"].get("pip_value", 0.01)
        if "/" in symbol:
            return max(price * 0.0005, 0.01)
        return base

    # ---------------------------------------------------------
    def scan_symbol(self, symbol):
        cfg = self.cfg
        tf = cfg["strategy"]["timeframe"]
        warmup = cfg["engine"].get("candle_warmup", 60)
        df = self.broker.bars(symbol, tf, warmup)
        if df is None or len(df) < warmup // 2:
            return None
        sig = generate_signal(df, cfg["strategy"])
        if sig:
            sig["symbol"] = symbol
            sig["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            return sig
        return None

    def enter(self, sig):
        cfg = self.cfg
        symbol = sig["symbol"]
        if symbol in self._mgmt:
            return
        if len(self._mgmt) >= cfg["risk"].get("max_open_trades", 3):
            self._log(f"max open trades reached, skipping {symbol}", "INFO")
            return

        # Crypto cannot be shorted on Alpaca (you can only sell crypto you
        # already hold). Since we never carry inventory into a short, skip
        # crypto SELL signals entirely — take crypto longs only.
        if "/" in symbol and sig["side"] == "sell":
            self._log(f"skip SELL {symbol} — crypto can't be shorted on Alpaca", "INFO")
            return

        equity = self.broker.equity()
        entry = sig["entry"]
        stop = sig["stop"]
        if stop <= 0 or entry <= 0:
            return

        # Enforce a minimum stop distance so a too-tight swing stop doesn't
        # produce an absurd position size (e.g. 50 SOL on one order).
        min_dist = entry * cfg["risk"].get("min_stop_pct", 0.005)
        if abs(entry - stop) < min_dist:
            stop = entry - min_dist if sig["side"] == "buy" else entry + min_dist

        qty = position_size(equity, entry, stop, cfg["risk"])
        if qty <= 0:
            return
        # target based on configured pips (scaled per asset type)
        pip_value = self._pip_value(symbol, entry)
        tp_pips = cfg["risk"].get("target_pips", 50)
        target = entry + tp_pips * pip_value if sig["side"] == "buy" else entry - tp_pips * pip_value

        order = self.broker.submit_market(symbol, sig["side"], round(qty, 6))
        self._mgmt[symbol] = {
            "side": sig["side"], "entry": entry, "qty": qty, "stop": stop,
            "target": target, "breakeven_done": False, "partial_done": False,
            "lock": None, "open_ts": time.time(),
        }
        STATE.add_order({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol, "side": sig["side"], "qty": round(qty, 6),
            "entry": round(entry, 6), "stop": round(stop, 6),
            "target": round(target, 6), "action": "ENTER",
            "reason": sig.get("reason", ""),
        })
        self._log(f"ENTER {sig['side'].upper()} {symbol} qty={qty:.4f} @ {entry:.4f} "
                  f"SL={stop:.4f} TP={target:.4f} ({sig.get('reason','')})")

    def manage(self, symbol):
        m = self._mgmt.get(symbol)
        if not m:
            return
        px = self.broker.latest_price(symbol)
        if px is None:
            return
        cfg = self.cfg
        pip_value = self._pip_value(symbol, px)
        entry = m["entry"]
        side = m["side"]
        move = (px - entry) if side == "buy" else (entry - px)
        move_pips = pip_distance(entry, px, pip_value)

        # 1) initial stop loss
        if (side == "buy" and px <= m["stop"]) or (side == "sell" and px >= m["stop"]):
            self.broker.close_position(symbol)
            pnl = (px - entry) * m["qty"] if side == "buy" else (entry - px) * m["qty"]
            STATE.record_trade(side, m["qty"], entry, px, pnl, "stop loss")
            self._log(f"STOP {symbol} @ {px:.4f} pnl={pnl:.2f}")
            self._mgmt.pop(symbol, None)
            return

        # 2) move stop to breakeven after +N pips
        be_pips = cfg["risk"].get("breakeven_after_pips", 20)
        if not m["breakeven_done"] and move_pips >= be_pips:
            m["stop"] = entry
            m["breakeven_done"] = True
            self._log(f"BREAKEVEN {symbol} — SL moved to entry after +{be_pips} pips")

        # 3) partial profit at +N pips
        pp_pips = cfg["risk"].get("partial_profit_pips", 30)
        frac = cfg["risk"].get("partial_fraction", 0.5)
        if not m["partial_done"] and move_pips >= pp_pips:
            m["partial_done"] = True
            lock_pips = cfg["risk"].get("lock_pips", 20)
            m["lock"] = entry + lock_pips * pip_value if side == "buy" else entry - lock_pips * pip_value
            # reduce position
            if hasattr(self.broker, "reduce_position"):
                self.broker.reduce_position(symbol, frac)
            else:
                self.broker.close_position(symbol)  # fallback: full close
            self._log(f"PARTIAL {symbol} — took {frac*100:.0f}% at +{pp_pips} pips, SL locked at +{lock_pips}")

        # 4) locked stop after partial
        if m["lock"] is not None:
            if (side == "buy" and px <= m["lock"]) or (side == "sell" and px >= m["lock"]):
                self.broker.close_position(symbol)
                self._log(f"LOCKED STOP {symbol} @ {px:.4f}")
                self._mgmt.pop(symbol, None)
                return

        # 5) take profit
        if (side == "buy" and px >= m["target"]) or (side == "sell" and px <= m["target"]):
            self.broker.close_position(symbol)
            pnl = (px - entry) * m["qty"] if side == "buy" else (entry - px) * m["qty"]
            STATE.record_trade(side, m["qty"], entry, px, pnl, "take profit")
            self._log(f"TARGET {symbol} @ {px:.4f} pnl={pnl:.2f}  #KeepItBlue")
            self._mgmt.pop(symbol, None)

    def refresh_positions(self):
        """Sync dashboard state with broker + management dict."""
        positions = []
        for symbol, m in self._mgmt.items():
            px = self.broker.latest_price(symbol)
            if px is None:
                px = m["entry"]
            pnl = (px - m["entry"]) * m["qty"] if m["side"] == "buy" else (m["entry"] - px) * m["qty"]
            positions.append({
                "symbol": symbol, "side": m["side"], "qty": round(m["qty"], 6),
                "entry": round(m["entry"], 6), "price": round(px, 6),
                "stop": round(m["stop"], 6), "target": round(m["target"], 6),
                "pnl": round(pnl, 2),
                "breakeven": m["breakeven_done"], "partial": m["partial_done"],
            })
        STATE.set(positions=positions)

    # ---------------------------------------------------------
    def tick(self):
        now = datetime.now(timezone.utc)
        # In simulation (mock data) the synthetic series isn't tied to real
        # session hours, so we skip gating there; real mode respects windows.
        session_open = in_session(self.cfg, now) or isinstance(self.broker, MockBroker)

        # Always refresh equity + last-scan timestamp, even when the session
        # is closed, so the dashboard shows a live account balance.
        try:
            eq = self.broker.equity()
            STATE.set(equity=round(eq, 2), last_scan=now.strftime("%H:%M:%S"))
        except Exception as e:
            self._log(f"equity fetch failed: {e}", "ERROR")

        if not session_open:
            # still manage open positions, just don't open new ones
            for s in list(self._mgmt.keys()):
                self.manage(s)
            self.refresh_positions()
            return

        for symbol in self._syms:
            try:
                sig = self.scan_symbol(symbol)
                if sig:
                    STATE.add_signal(sig)
                    if symbol not in self._mgmt:
                        self.enter(sig)
            except Exception as e:
                self._log(f"error scanning {symbol}: {e}", "ERROR")
                traceback.print_exc()

        # manage all open positions
        for s in list(self._mgmt.keys()):
            try:
                self.manage(s)
            except Exception as e:
                self._log(f"error managing {s}: {e}", "ERROR")

        self.refresh_positions()
        STATE.set(equity=round(self.broker.equity(), 2), last_scan=now.strftime("%H:%M:%S"))

    # ---------------------------------------------------------
    def run(self):
        STATE.set(running=True, mode=self.broker.mode,
                  started_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        self._log(f"714 Method engine started — mode={self.broker.mode}, symbols={self._syms}")
        poll = self.cfg["engine"].get("poll_seconds", 30)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:
                self._log(f"tick error: {e}", "ERROR")
                traceback.print_exc()
            self._stop.wait(poll)
        self._log("engine stopped")

    def stop(self):
        self._stop.set()


def load_config(path="config.yaml"):
    import yaml
    with open(path) as f:
        cfg = yaml.safe_load(f)
    # Allow overriding broker mode via environment variable (e.g. set
    # BROKER_MODE=paper or BROKER_MODE=live in Render's dashboard) so you
    # can toggle paper/live without editing files or redeploying code.
    mode = os.getenv("BROKER_MODE")
    if mode and mode.strip().lower() in ("paper", "live"):
        cfg["broker"]["mode"] = mode.strip().lower()
    return cfg
