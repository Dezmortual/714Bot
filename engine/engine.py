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


def session_status(cfg, now_utc):
    """Return a dict describing the current + next session window (for the
    dashboard), in the strategy's timezone."""
    tz = pytz.timezone(cfg["strategy"].get("session_timezone", "Africa/Johannesburg"))
    local = now_utc.astimezone(tz)
    windows = cfg["strategy"].get("session_windows", [])
    if not cfg["strategy"].get("use_session_filter", True) or not windows:
        return {"in_session": True, "label": "always active", "next": None}

    hhmm = local.strftime("%H:%M")
    now_min = int(hhmm[:2]) * 60 + int(hhmm[3:])

    # current window?
    for w in windows:
        a, b = w.split("-")
        a_min = int(a[:2]) * 60 + int(a[3:])
        b_min = int(b[:2]) * 60 + int(b[3:])
        if a_min <= now_min <= b_min:
            return {"in_session": True, "label": f"trading window {w}", "next": None}

    # next upcoming window (today)
    for w in windows:
        a, _ = w.split("-")
        a_min = int(a[:2]) * 60 + int(a[3:])
        if a_min > now_min:
            return {"in_session": False, "label": "waiting", "next": w}

    # all passed today -> first window tomorrow
    return {"in_session": False, "label": "waiting (all windows passed today)",
            "next": windows[0] + " (tomorrow)"}


class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.broker = _make_broker(cfg)
        self._stop = threading.Event()
        # per-symbol management state:
        # {symbol: {side, entry, qty, stop, target, breakeven_done, partial_done, lock}}
        self._mgmt = {}
        self._syms = cfg["strategy"]["symbols"]
        # signal cooldown: symbol -> last (side, timestamp) so the same
        # signal isn't re-logged/processed every poll.
        self._signal_cooldown = {}
        self._cooldown_secs = cfg["engine"].get("signal_cooldown_secs", 300)
        # re-entry cooldown: symbol -> timestamp of last exit, so the bot
        # doesn't immediately re-buy after a stop/target (prevents wash
        # trades and churn).
        self._last_exit = {}
        self._reentry_secs = cfg["engine"].get("reentry_cooldown_secs", 300)
        self._reconcile_positions()

    def _reconcile_positions(self):
        """Adopt any positions already open at the broker (e.g. left over
        from a previous deploy/restart) so we don't duplicate entries and
        we can keep managing them."""
        if not hasattr(self.broker, "open_positions"):
            return
        try:
            for p in self.broker.open_positions():
                sym = p["symbol"]
                if sym in self._mgmt:
                    continue
                # Reconciled positions get a conservative stop/target derived
                # from a 1% price distance so they're safely managed until a
                # fresh signal takes over.
                dist = p["entry"] * 0.01
                rr = self.cfg["risk"].get("risk_reward_ratio", 2.0)
                stop = p["entry"] - dist if p["side"] == "buy" else p["entry"] + dist
                target = p["entry"] + dist * rr if p["side"] == "buy" else p["entry"] - dist * rr
                self._mgmt[sym] = {
                    "side": p["side"], "entry": p["entry"], "qty": p["qty"],
                    "stop": stop, "target": target, "stop_dist": dist,
                    "breakeven_done": True, "partial_done": True,
                    "lock": p["entry"], "open_ts": time.time(),
                }
                self._log(f"reconciled existing position {sym} "
                          f"{p['side']} qty={p['qty']:.4f} @ {p['entry']:.4f}")
        except Exception as e:
            self._log(f"position reconcile failed: {e}", "WARN")

    # ---------------------------------------------------------
    def _log(self, msg, level="INFO"):
        STATE.add_log(msg, level)

    def _pip_value(self, symbol, price):
        """Pip size for a symbol. Stocks use the configured absolute pip
        (default $0.01). Crypto uses a RELATIVE pip (0.05% of price) with
        NO absolute floor, so it scales correctly for cheap coins like DOGE
        (~$0.08) as well as expensive ones like BTC (~$60k)."""
        base = self.cfg["risk"].get("pip_value", 0.01)
        if "/" in symbol:
            return price * 0.0005
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
            # attach ATR so the engine can size a volatility-based stop
            a = atr(df, cfg["risk"].get("atr_period", 14))
            sig["atr"] = float(a.iloc[-1]) if a.notna().iloc[-1] else None
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

        # Re-entry cooldown: don't re-enter a symbol right after exiting it
        # (prevents buy->stop->buy churn and Alpaca wash-trade rejections).
        last_exit = self._last_exit.get(symbol, 0)
        if time.time() - last_exit < self._reentry_secs:
            return

        # Crypto cannot be shorted on Alpaca (you can only sell crypto you
        # already hold). Since we never carry inventory into a short, skip
        # crypto SELL signals entirely — take crypto longs only.
        if "/" in symbol and sig["side"] == "sell":
            self._log(f"skip SELL {symbol} — crypto can't be shorted on Alpaca", "INFO")
            return

        equity = self.broker.equity()
        entry = sig["entry"]
        swing_stop = sig["stop"]
        side = sig["side"]
        if swing_stop <= 0 or entry <= 0:
            return

        risk_cfg = cfg["risk"]

        # ---- Stop distance (option 2: ATR-based minimum) ----
        # Start with the swing-pattern stop, then enforce a floor of
        # ATR * atr_stop_mult so stops aren't too tight (volatility-aware).
        swing_dist = abs(entry - swing_stop)
        atr_val = sig.get("atr") or 0.0
        atr_min = atr_val * risk_cfg.get("atr_stop_mult", 2.0)
        pct_min = entry * risk_cfg.get("min_stop_pct", 0.003)
        stop_dist = max(swing_dist, atr_min, pct_min)

        stop = entry - stop_dist if side == "buy" else entry + stop_dist

        # ---- Target (option 1: risk:reward) ----
        rr = risk_cfg.get("risk_reward_ratio", 2.0)
        target = entry + stop_dist * rr if side == "buy" else entry - stop_dist * rr

        # ---- Position size ----
        qty = position_size(equity, entry, stop, risk_cfg)
        if qty <= 0:
            return

        # ---- Option 3: buying-power guard ----
        # Scale back (or skip) if the order's notional exceeds available
        # cash. Crypto is non-marginable and ties up cash 1:1, which is
        # what caused the "insufficient balance for USD" rejections.
        try:
            cash = self.broker.available_cash()
        except Exception:
            cash = None
        if cash is not None:
            notional = qty * entry
            if cash <= 0:
                self._log(f"skip {symbol} — no available cash", "WARN")
                return
            if notional > cash * 0.98:
                qty = (cash * 0.98) / entry
                if qty <= 0:
                    return
                self._log(f"{symbol} scaled down to available cash: qty={qty:.4f}")

        order = self.broker.submit_market(symbol, side, round(qty, 6))
        self._mgmt[symbol] = {
            "side": side, "entry": entry, "qty": qty, "stop": stop,
            "target": target, "stop_dist": stop_dist,
            "breakeven_done": False, "partial_done": False,
            "lock": None, "open_ts": time.time(),
        }
        STATE.add_order({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol, "side": side, "qty": round(qty, 6),
            "entry": round(entry, 6), "stop": round(stop, 6),
            "target": round(target, 6), "action": "ENTER",
            "reason": sig.get("reason", ""),
        })
        self._log(f"ENTER {side.upper()} {symbol} qty={qty:.4f} @ {entry:.4f} "
                  f"SL={stop:.4f} TP={target:.4f} (R={rr}, dist={stop_dist:.4f})")

    def manage(self, symbol):
        m = self._mgmt.get(symbol)
        if not m:
            return
        px = self.broker.latest_price(symbol)
        if px is None:
            return
        cfg = self.cfg
        entry = m["entry"]
        side = m["side"]
        stop_dist = m.get("stop_dist") or abs(entry - m["stop"])
        if stop_dist <= 0:
            stop_dist = 1e-9
        move = (px - entry) if side == "buy" else (entry - px)
        move_r = move / stop_dist  # profit/loss in R-multiples

        # 1) initial stop loss
        if (side == "buy" and px <= m["stop"]) or (side == "sell" and px >= m["stop"]):
            self.broker.close_position(symbol)
            pnl = (px - entry) * m["qty"] if side == "buy" else (entry - px) * m["qty"]
            STATE.record_trade(side, m["qty"], entry, px, pnl, "stop loss")
            self._log(f"STOP {symbol} @ {px:.4f} pnl={pnl:.2f}")
            self._last_exit[symbol] = time.time()
            self._mgmt.pop(symbol, None)
            return

        # 2) move stop to breakeven at +breakeven_r R
        be_r = cfg["risk"].get("breakeven_r", 0.5)
        if not m["breakeven_done"] and move_r >= be_r:
            m["stop"] = entry
            m["breakeven_done"] = True
            self._log(f"BREAKEVEN {symbol} — SL to entry at +{be_r}R")

        # 3) partial profit at +partial_r R
        pp_r = cfg["risk"].get("partial_r", 1.0)
        frac = cfg["risk"].get("partial_fraction", 0.5)
        if not m["partial_done"] and move_r >= pp_r:
            m["partial_done"] = True
            lock_r = cfg["risk"].get("lock_r", 0.5)
            m["lock"] = entry + lock_r * stop_dist if side == "buy" else entry - lock_r * stop_dist
            if hasattr(self.broker, "reduce_position"):
                self.broker.reduce_position(symbol, frac)
            else:
                self.broker.close_position(symbol)
            self._log(f"PARTIAL {symbol} — took {frac*100:.0f}% at +{pp_r}R, SL locked at +{lock_r}R")

        # 4) locked stop after partial
        if m["lock"] is not None:
            if (side == "buy" and px <= m["lock"]) or (side == "sell" and px >= m["lock"]):
                self.broker.close_position(symbol)
                self._log(f"LOCKED STOP {symbol} @ {px:.4f}")
                self._last_exit[symbol] = time.time()
                self._mgmt.pop(symbol, None)
                return

        # 5) take profit
        if (side == "buy" and px >= m["target"]) or (side == "sell" and px <= m["target"]):
            self.broker.close_position(symbol)
            pnl = (px - entry) * m["qty"] if side == "buy" else (entry - px) * m["qty"]
            STATE.record_trade(side, m["qty"], entry, px, pnl, "take profit")
            self._log(f"TARGET {symbol} @ {px:.4f} pnl={pnl:.2f}  #KeepItBlue")
            self._last_exit[symbol] = time.time()
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
        # Publish session status for the dashboard (always).
        STATE.set(session=session_status(self.cfg, now))

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
                    # Cooldown: don't re-fire the same symbol+side signal
                    # within the cooldown window (stops log spam + duplicate
                    # processing while a pattern persists).
                    last = self._signal_cooldown.get(symbol)
                    if last and last[0] == sig["side"] and (time.time() - last[1]) < self._cooldown_secs:
                        continue
                    self._signal_cooldown[symbol] = (sig["side"], time.time())
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
