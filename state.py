# ============================================================
#  714 METHOD — shared bot state (thread-safe)
# ============================================================
import threading
import time
import csv
import os


class BotState:
    """Single shared object read by the engine (writer) and dashboard
    (reader). A lock protects every mutation."""

    def __init__(self):
        self._lock = threading.RLock()
        self.running = False
        self.mode = "paper"
        self.started_at = None
        self.last_scan = None
        self.equity = None
        self.signals = []          # list of dicts, newest first (max 200)
        self.positions = []        # current open positions
        self.orders = []           # recent orders (max 200)
        self.log = []              # human-readable log lines (max 500)
        self.stats = {"wins": 0, "losses": 0, "breakeven": 0, "pnl": 0.0}

    # ---- helpers ----
    def _now(self):
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def push(self, attr, item, maxlen=200):
        with self._lock:
            lst = getattr(self, attr)
            lst.insert(0, item)
            del lst[maxlen:]

    def add_log(self, msg, level="INFO"):
        self.push("log", {"ts": self._now(), "level": level, "msg": msg}, 500)

    def add_signal(self, sig):
        self.push("signals", sig, 200)

    def add_order(self, order):
        self.push("orders", order, 200)

    def record_trade(self, side, qty, entry, exit_price, pnl, reason):
        with self._lock:
            if pnl > 0:
                self.stats["wins"] += 1
            elif pnl < 0:
                self.stats["losses"] += 1
            else:
                self.stats["breakeven"] += 1
            self.stats["pnl"] += pnl
        self.push(
            "orders",
            {
                "ts": self._now(),
                "side": side,
                "qty": qty,
                "entry": entry,
                "exit": exit_price,
                "pnl": round(pnl, 2),
                "reason": reason,
            },
            200,
        )
        # persist to csv
        path = "data/trades.csv"
        try:
            os.makedirs("data", exist_ok=True)
            new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(
                        ["ts", "side", "qty", "entry", "exit", "pnl", "reason"]
                    )
                w.writerow(
                    [self._now(), side, qty, entry, exit_price, round(pnl, 2), reason]
                )
        except Exception as e:
            self.add_log(f"failed to write trade csv: {e}", "WARN")

    # ---- snapshots (deep-copied under lock for safe reads) ----
    def snapshot(self):
        with self._lock:
            return {
                "running": self.running,
                "mode": self.mode,
                "started_at": self.started_at,
                "last_scan": self.last_scan,
                "equity": self.equity,
                "signals": list(self.signals),
                "positions": list(self.positions),
                "orders": list(self.orders),
                "log": list(self.log),
                "stats": dict(self.stats),
            }


STATE = BotState()
