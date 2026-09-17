"""
DTC v1.36 strategy core — a faithful Python port of the Pine Script v5
indicator logic.

Pine source behaviour that this module reproduces:

  * Six EMAs on close (default 30/35/40/45/50/60).
  * ``bullish_trend`` = ema1 > ema2 > ... > ema6  (strict ordering, "EMA fan")
  * ``bearish_trend`` = mirror condition.
  * Long signal  = ``bullish_trend`` turns True on a confirmed bar.
    Short signal = ``bearish_trend`` turns True on a confirmed bar.
  * No same-side repeat: ``signal_state`` blocks a second long while the
    previous signal was long (signals therefore alternate long/short).
  * Optional ATR filter: ``ta.atr(atrPeriod)`` must exceed ``atr_min``.
  * Levels:
        SL  = entry ∓ stop_loss_pct %
        TPk = entry ± stop_loss_pct * tp_multiplier[k] %

Notes on exact-Pine equality: ``ta.ema`` seeds with the first value and
uses alpha = 2/(n+1) (== pandas ewm(span=n, adjust=False)); ``ta.atr`` is
Wilder's RMA of true range (== ewm(alpha=1/n, adjust=False)).  Both only
converge to Pine values after a warm-up of a few hundred bars, which the
engine enforces via ``warmup_bars``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
# Indicator helpers (Pine equivalents)
# ----------------------------------------------------------------------

def ema(series: pd.Series, length: int) -> pd.Series:
    """Pine ta.ema equivalent."""
    return series.ewm(span=int(length), adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, length: int) -> pd.Series:
    """Pine ta.atr equivalent (Wilder's RMA of true range)."""
    return true_range(df).ewm(alpha=1.0 / int(length), adjust=False).mean()


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

@dataclass
class StrategyConfig:
    ema_lengths: List[int] = field(
        default_factory=lambda: [30, 35, 40, 45, 50, 60]
    )
    stop_loss_pct: float = 0.25                       # Pine: stopLossVal
    tp_multipliers: List[float] = field(
        default_factory=lambda: [1.0, 2.0, 3.0, 4.0]
    )
    use_atr_filter: bool = True
    atr_period: int = 14
    atr_min: float = 0.5                              # Pine: atrMin (absolute price units)
    atr_min_pct: Optional[float] = None               # optional: % of price instead of absolute

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyConfig":
        d = d or {}
        return cls(
            ema_lengths=[int(x) for x in d.get("ema_lengths", [30, 35, 40, 45, 50, 60])],
            stop_loss_pct=float(d.get("stop_loss_pct", 0.25)),
            tp_multipliers=[float(x) for x in d.get("tp_multipliers", [1.0, 2.0, 3.0, 4.0])],
            use_atr_filter=bool(d.get("use_atr_filter", True)),
            atr_period=int(d.get("atr_period", 14)),
            atr_min=float(d.get("atr_min", 0.5)),
            atr_min_pct=d.get("atr_min_pct", None),
        )

    def warmup_bars(self) -> int:
        """Bars needed before EMA/ATR values are stable enough to trade on."""
        return max(self.ema_lengths) * 3 + self.atr_period * 2 + 5


# ----------------------------------------------------------------------
# Signal levels (the Pine SL/TP math)
# ----------------------------------------------------------------------

@dataclass
class Levels:
    direction: int            # +1 long, -1 short
    entry: float
    sl: float
    tps: List[float]          # TP1..TP4

    def as_dict(self) -> dict:
        return {
            "direction": self.direction,
            "entry": self.entry,
            "sl": self.sl,
            "tps": list(self.tps),
        }


def make_levels(direction: int, entry: float, cfg: StrategyConfig) -> Levels:
    """Exact Pine formulas:

        long :  sl = entry * (1 - sp%)          tp_k = entry * (1 + sp*mult_k %)
        short:  sl = entry * (1 + sp%)          tp_k = entry * (1 - sp*mult_k %)
    """
    sp = cfg.stop_loss_pct / 100.0
    if direction == 1:
        sl = entry * (1.0 - sp)
        tps = [entry * (1.0 + sp * m) for m in cfg.tp_multipliers]
    else:
        sl = entry * (1.0 + sp)
        tps = [entry * (1.0 - sp * m) for m in cfg.tp_multipliers]
    return Levels(direction=direction, entry=float(entry), sl=float(sl), tps=tps)


# ----------------------------------------------------------------------
# Enrichment — adds EMAs, ATR, trend flags and the ATR gate to a DataFrame
# ----------------------------------------------------------------------

def enrich(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Return a copy of ``df`` (must have open/high/low/close columns) with
    strategy columns appended: ema_1..ema_N, atr, bullish, bearish, atr_ok."""
    out = df.copy()
    emas = []
    for i, n in enumerate(cfg.ema_lengths, start=1):
        col = f"ema_{i}"
        out[col] = ema(out["close"], n)
        emas.append(out[col])

    # bullish: ema_1 > ema_2 > ... > ema_N ; bearish: strict mirror
    bull = pd.Series(True, index=out.index)
    bear = pd.Series(True, index=out.index)
    for a, b in zip(emas[:-1], emas[1:]):
        bull &= a > b
        bear &= a < b
    out["bullish"] = bull
    out["bearish"] = bear

    out["atr"] = atr(out, cfg.atr_period)

    if not cfg.use_atr_filter:
        out["atr_ok"] = True
    elif cfg.atr_min_pct is not None and float(cfg.atr_min_pct) > 0:
        out["atr_ok"] = out["atr"] > out["close"] * float(cfg.atr_min_pct) / 100.0
    else:
        out["atr_ok"] = out["atr"] > cfg.atr_min

    return out


# ----------------------------------------------------------------------
# Signals
# ----------------------------------------------------------------------

@dataclass
class Signal:
    direction: int            # +1 long / -1 short
    price: float              # entry price (close of the signal bar, as in Pine)
    bar_time: pd.Timestamp
    levels: Levels


class DTCStrategy:
    """Stateful strategy evaluator (live use).

    ``signal_state`` mirrors the Pine ``var int signal_state`` so that
    restarts don't re-fire the same signal:  0 none / +1 long / -1 short.
    """

    def __init__(self, cfg: StrategyConfig, signal_state: int = 0):
        self.cfg = cfg
        self.signal_state = int(signal_state)

    def process_bar(self, df: pd.DataFrame, i: int = -1) -> Optional[Signal]:
        """Evaluate bar ``i`` of an ENRICHED frame (see :func:`enrich`).

        Returns a :class:`Signal` when a fresh long/short fires on that bar;
        otherwise None.  Updates ``self.signal_state`` when a signal fires.
        """
        n = len(df)
        if i < 0:
            i = n + i
        if i < 1 or n < 2:
            return None

        bull = bool(df["bullish"].iat[i])
        bear = bool(df["bearish"].iat[i])
        bull_prev = bool(df["bullish"].iat[i - 1])
        bear_prev = bool(df["bearish"].iat[i - 1])
        atr_ok = bool(df["atr_ok"].iat[i])

        # Pine:  long_signal_raw  = not bullish_trend[1] and bullish_trend
        #        short_signal_raw = not bearish_trend[1] and bearish_trend
        #        ... and signal_state != X and atr_ok
        sig = 0
        if bull and not bull_prev and self.signal_state != 1 and atr_ok:
            sig = 1
        elif bear and not bear_prev and self.signal_state != -1 and atr_ok:
            sig = -1

        if sig:
            self.signal_state = sig
            price = float(df["close"].iat[i])
            bar_time = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i
            return Signal(
                direction=sig,
                price=price,
                bar_time=bar_time,
                levels=make_levels(sig, price, self.cfg),
            )
        return None


def generate_signals(df: pd.DataFrame, cfg: StrategyConfig) -> np.ndarray:
    """Vectorised version for the backtester: int array of 0/+1/-1 per bar.

    Reproduces the Pine signal_state alternation from scratch (state = 0
    at the first bar).
    """
    e = enrich(df, cfg)
    bull = e["bullish"].to_numpy(dtype=bool)
    bear = e["bearish"].to_numpy(dtype=bool)
    ok = e["atr_ok"].to_numpy(dtype=bool)

    sig = np.zeros(len(e), dtype=int)
    state = 0
    for i in range(1, len(e)):
        if bull[i] and not bull[i - 1] and state != 1 and ok[i]:
            sig[i] = 1
            state = 1
        elif bear[i] and not bear[i - 1] and state != -1 and ok[i]:
            sig[i] = -1
            state = -1
    return sig
