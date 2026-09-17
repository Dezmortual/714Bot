# ============================================================
#  DTC v1.36 — Automated Trading Engine
#  Executes trades based on 6-EMA ribbon trends, ATR filters,
#  and multi-target TP (TP1/TP2/TP3/TP4) + SL management.
# ============================================================
import os
import time
import threading
import traceback
from datetime import datetime, timezone

import pandas as pd
import pytz

from state import STATE
from engine.strategy import generate_signal, compute_dtc_signals, calculate_mtf_trend
from engine.risk import position_size
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


class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.broker = _make_broker(cfg)
        self._stop = threading.Event()
        # per-symbol management state:
        # {symbol: {side, entry, qty, orig_qty, stop, tp1, tp2, tp3, tp4, tp_hits: set()}}
        self._mgmt = {}
        self._syms = cfg["strategy"]["symbols"]
        self._signal_cooldown = {}
        self._cooldown_secs = cfg["engine"].get("signal_cooldown_secs", 180)
        self._last_exit = {}
        self._reentry_secs = cfg["engine"].get("reentry_cooldown_secs", 120)
        self._reconcile_positions()

    def _reconcile_positions(self):
        """Adopt any positions already open at the broker."""
        if not hasattr(self.broker, "open_positions"):
            return
        try:
            for p in self.broker.open_positions():
                sym = p["symbol"]
                if sym in self._mgmt:
                    continue
                sl_pct = float(self.cfg["strategy"].get("stop_loss_pct", 0.25)) / 100.0
                dist = p["entry"] * sl_pct
                stop = p["entry"] - dist if p["side"] == "buy" else p["entry"] + dist
                tp1 = p["entry"] + dist if p["side"] == "buy" else p["entry"] - dist
                tp2 = p["entry"] + dist * 2 if p["side"] == "buy" else p["entry"] - dist * 2
                tp3 = p["entry"] + dist * 3 if p["side"] == "buy" else p["entry"] - dist * 3
                tp4 = p["entry"] + dist * 4 if p["side"] == "buy" else p["entry"] - dist * 4
                self._mgmt[sym] = {
                    "side": p["side"], "entry": p["entry"], "qty": p["qty"],
                    "orig_qty": p["qty"], "stop": stop, "tp1": tp1, "tp2": tp2,
                    "tp3": tp3, "tp4": tp4, "tp_hits": set(),
                    "open_ts": time.time(),
                }
                self._log(f"reconciled existing position {sym} {p['side']} qty={p['qty']:.4f} @ {p['entry']:.4f}")
        except Exception as e:
            self._log(f"position reconcile failed: {e}", "WARN")

    def _log(self, msg, level="INFO"):
        STATE.add_log(msg, level)

    def scan_symbol(self, symbol):
        cfg = self.cfg
        tf = cfg["strategy"].get("timeframe", "15Min")
        warmup = cfg["engine"].get("candle_warmup", 90)
        df = self.broker.bars(symbol, tf, warmup)
        if df is None or len(df) < 65:
            return None

        # Gather MTF data if supported
        mtf_bars = {}
        mtf_tfs = cfg["strategy"].get("mtf_timeframes", {"15": "15Min", "30": "30Min", "60": "1Hour"})
        for label, mtf_tf in mtf_tfs.items():
            if mtf_tf != tf:
                try:
                    mdf = self.broker.bars(symbol, mtf_tf, 60)
                    if mdf is not None and len(mdf) >= 50:
                        mtf_bars[label] = mdf
                except Exception:
                    pass
            else:
                mtf_bars[label] = df

        sig = generate_signal(df, cfg["strategy"], mtf_bars)
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
        if len(self._mgmt) >= cfg["risk"].get("max_open_trades", 4):
            self._log(f"max open trades reached, skipping {symbol}", "INFO")
            return

        last_exit = self._last_exit.get(symbol, 0)
        if time.time() - last_exit < self._reentry_secs:
            return

        # Alpaca crypto cannot be shorted
        if "/" in symbol and sig["side"] == "sell":
            self._log(f"skip SELL {symbol} — crypto cannot be shorted on Alpaca", "INFO")
            return

        equity = self.broker.equity()
        entry = sig["entry"]
        stop = sig["stop"]
        sig_side = str(sig["side"]).lower()
        if sig_side not in ("buy", "sell"):
            return
        if stop <= 0 or entry <= 0:
            return

        risk_cfg = cfg["risk"]
        qty = position_size(equity, entry, stop, risk_cfg)
        if qty <= 0:
            return

        # Buying-power guard
        is_crypto = "/" in symbol
        try:
            funds = self.broker.available_cash() if is_crypto else self.broker.buying_power()
        except Exception:
            funds = None
        if funds is not None:
            if funds <= 0:
                self._log(f"skip {symbol} — no buying power left", "WARN")
                return
            if qty * entry > funds * 0.95:
                qty = (funds * 0.95) / entry
                if qty <= 0:
                    self._log(f"skip {symbol} — insufficient buying power", "WARN")
                    return
                self._log(f"{symbol} scaled down to buying power: qty={qty:.4f}", "WARN")

        order = self.broker.submit_market(symbol, side, round(qty, 6))
        self._mgmt[symbol] = {
            "side": side,
            "entry": entry,
            "qty": qty,
            "orig_qty": qty,
            "stop": stop,
            "tp1": sig["tp1"],
            "tp2": sig["tp2"],
            "tp3": sig["tp3"],
            "tp4": sig["tp4"],
            "tp_hits": set(),
            "open_ts": time.time(),
        }
        STATE.add_order({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol, "side": side, "qty": round(qty, 6),
            "entry": round(entry, 6), "stop": round(stop, 6),
            "tp1": round(sig["tp1"], 6), "tp4": round(sig["tp4"], 6),
            "action": "ENTER",
            "reason": sig.get("reason", "DTC 1.36 Entry"),
        })
        self._log(f"ENTER {side.upper()} {symbol} qty={qty:.4f} @ {entry:.4f} | SL={stop:.4f} | TP1={sig['tp1']:.4f} TP2={sig['tp2']:.4f} TP3={sig['tp3']:.4f} TP4={sig['tp4']:.4f}")

    def manage(self, symbol):
        m = self._mgmt.get(symbol)
        if not m:
            return
        px = self.broker.latest_price(symbol)
        if px is None:
            return

        entry = m["entry"]
        side = m["side"]
        qty = m["qty"]

        # 1. Stop loss check
        hit_sl = (side == "buy" and px <= m["stop"]) or (side == "sell" and px >= m["stop"])
        if hit_sl:
            self.broker.close_position(symbol)
            pnl = (px - entry) * qty if side == "buy" else (entry - px) * qty
            STATE.record_trade(side, qty, entry, px, pnl, "stop loss")
            self._log(f"STOP LOSS {symbol} @ {px:.4f} pnl=${pnl:.2f}")
            self._last_exit[symbol] = time.time()
            self._mgmt.pop(symbol, None)
            return

        # 2. Multi-Target Profit Management (TP1, TP2, TP3, TP4)
        tp_levels = [
            (1, m["tp1"]),
            (2, m["tp2"]),
            (3, m["tp3"]),
            (4, m["tp4"]),
        ]

        # Check TP1 through TP4
        for tp_num, tp_val in tp_levels:
            if tp_num in m["tp_hits"]:
                continue

            hit_tp = (side == "buy" and px >= tp_val) or (side == "sell" and px <= tp_val)
            if hit_tp:
                m["tp_hits"].add(tp_num)
                # When TP1 is hit, move Stop Loss to breakeven (entry)
                if tp_num == 1:
                    m["stop"] = entry
                    self._log(f"TP1 HIT {symbol} @ {px:.4f} — Stop Loss moved to BREAKEVEN ({entry:.4f})")

                # When TP2 is hit, trail Stop Loss to TP1
                elif tp_num == 2:
                    m["stop"] = m["tp1"]
                    self._log(f"TP2 HIT {symbol} @ {px:.4f} — Trailing Stop moved to TP1 ({m['tp1']:.4f})")

                # When TP3 is hit, trail Stop Loss to TP2
                elif tp_num == 3:
                    m["stop"] = m["tp2"]
                    self._log(f"TP3 HIT {symbol} @ {px:.4f} — Trailing Stop moved to TP2 ({m['tp2']:.4f})")

                # When TP4 is reached, close entire remaining position
                elif tp_num == 4:
                    self.broker.close_position(symbol)
                    pnl = (px - entry) * m["qty"] if side == "buy" else (entry - px) * m["qty"]
                    STATE.record_trade(side, m["qty"], entry, px, pnl, "TP4 Full Target")
                    self._log(f"TP4 FINAL TARGET {symbol} @ {px:.4f} pnl=${pnl:.2f} — Position Closed!")
                    self._last_exit[symbol] = time.time()
                    self._mgmt.pop(symbol, None)
                    return

                # Partial close on TP1-TP3: take 25% of original quantity
                partial_frac = 0.25
                if hasattr(self.broker, "reduce_position") and m["qty"] > 0:
                    reduce_qty = min(m["orig_qty"] * partial_frac, m["qty"] * 0.9)
                    if reduce_qty > 0:
                        self.broker.reduce_position(symbol, reduce_qty / m["qty"])
                        m["qty"] -= reduce_qty
                        self._log(f"PARTIAL TP{tp_num} {symbol}: took 25% profit, remaining qty={m['qty']:.4f}")

    def refresh_positions(self):
        """Sync dashboard state with positions and active targets."""
        positions = []
        for symbol, m in self._mgmt.items():
            px = self.broker.latest_price(symbol) or m["entry"]
            pnl = (px - m["entry"]) * m["qty"] if m["side"] == "buy" else (m["entry"] - px) * m["qty"]
            positions.append({
                "symbol": symbol,
                "side": m["side"],
                "qty": round(m["qty"], 6),
                "entry": round(m["entry"], 6),
                "price": round(px, 6),
                "stop": round(m["stop"], 6),
                "tp1": round(m["tp1"], 6),
                "tp2": round(m["tp2"], 6),
                "tp3": round(m["tp3"], 6),
                "tp4": round(m["tp4"], 6),
                "tp_hits": list(m["tp_hits"]),
                "target": round(m["tp4"], 6),
                "pnl": round(pnl, 2),
                "breakeven": 1 in m["tp_hits"],
                "partial": len(m["tp_hits"]) > 0,
            })
        STATE.set(positions=positions)

    def tick(self):
        now = datetime.now(timezone.utc)
        try:
            eq = self.broker.equity()
            STATE.set(equity=round(eq, 2), last_scan=now.strftime("%H:%M:%S"))
        except Exception as e:
            self._log(f"equity fetch failed: {e}", "ERROR")

        for symbol in self._syms:
            try:
                sig = self.scan_symbol(symbol)
                if sig:
                    last = self._signal_cooldown.get(symbol)
                    if last and last[0] == sig["side"] and (time.time() - last[1]) < self._cooldown_secs:
                        continue
                    self._signal_cooldown[symbol] = (sig["side"], time.time())
                    STATE.add_signal(sig)
                    if symbol not in self._mgmt:
                        self.enter(sig)
            except Exception as e:
                msg = str(e)
                if "insufficient buying power" in msg or "insufficient balance" in msg:
                    self._log(f"LOW FUNDS: {symbol} — insufficient buying power", "WARN")
                else:
                    self._log(f"error scanning {symbol}: {e}", "ERROR")
                    traceback.print_exc()

        # Manage positions
        for s in list(self._mgmt.keys()):
            try:
                self.manage(s)
            except Exception as e:
                self._log(f"error managing {s}: {e}", "ERROR")

        self.refresh_positions()
        STATE.set(equity=round(self.broker.equity(), 2), last_scan=now.strftime("%H:%M:%S"))

    def run(self):
        STATE.set(running=True, mode=self.broker.mode,
                  started_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        self._log(f"DTC v1.36 Trading Robot started — mode={self.broker.mode}, symbols={self._syms}")
        poll = self.cfg["engine"].get("poll_seconds", 15)
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
    mode = os.getenv("BROKER_MODE")
    if mode and mode.strip().lower() in ("paper", "live"):
        cfg["broker"]["mode"] = mode.strip().lower()
    return cfg
