"""
Position bookkeeping shared by the backtester and the paper/live engines.

An :class:`OpenPosition` tracks the remaining quantity and which of the
four take-profit levels were already hit (each TP closes 25% of the
initial quantity — the bot's execution of the Pine TP1..TP4 lines).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from .strategy import Levels

TP_FRACTIONS = [0.25, 0.25, 0.25, 0.25]   # TP1..TP4 each close 25% of the position


@dataclass
class Fill:
    time: Any
    price: float
    qty: float
    side: str          # "buy" | "sell"
    reason: str        # ENTRY | TP1..TP4 | SL | REVERSAL | CLOSE | EOD
    fee: float
    pnl: float         # realised pnl of this fill BEFORE fee (entry fills = 0)

    def as_dict(self) -> dict:
        return {
            "time": str(self.time),
            "price": self.price,
            "qty": self.qty,
            "side": self.side,
            "reason": self.reason,
            "fee": self.fee,
            "pnl": self.pnl,
        }


@dataclass
class OpenPosition:
    direction: int                 # +1 long / -1 short
    entry: float
    sl: float
    tps: List[float]
    initial_qty: float
    qty: float                     # remaining qty
    entry_time: Any
    entry_fee: float = 0.0
    risk_cash: float = 0.0         # cash risked (qty * stop distance) -> R multiples
    tp_hit: List[bool] = field(default_factory=lambda: [False, False, False, False])

    @classmethod
    def from_levels(cls, levels: Levels, qty: float, entry_time: Any,
                    entry_fee: float, risk_cash: float) -> "OpenPosition":
        return cls(
            direction=levels.direction,
            entry=levels.entry,
            sl=levels.sl,
            tps=list(levels.tps),
            initial_qty=float(qty),
            qty=float(qty),
            entry_time=entry_time,
            entry_fee=float(entry_fee),
            risk_cash=float(risk_cash),
        )

    @property
    def side(self) -> str:
        return "long" if self.direction == 1 else "short"

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "entry": self.entry,
            "sl": self.sl,
            "tps": list(self.tps),
            "initial_qty": self.initial_qty,
            "qty": self.qty,
            "entry_time": str(self.entry_time),
            "entry_fee": self.entry_fee,
            "risk_cash": self.risk_cash,
            "tp_hit": list(self.tp_hit),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OpenPosition":
        return cls(
            direction=int(d["direction"]),
            entry=float(d["entry"]),
            sl=float(d["sl"]),
            tps=[float(x) for x in d["tps"]],
            initial_qty=float(d["initial_qty"]),
            qty=float(d["qty"]),
            entry_time=d.get("entry_time"),
            entry_fee=float(d.get("entry_fee", 0.0)),
            risk_cash=float(d.get("risk_cash", 0.0)),
            tp_hit=[bool(x) for x in d.get("tp_hit", [False] * 4)],
        )


def close_fill(pos: OpenPosition, qty: float, price: float, time: Any,
               reason: str, fee_pct: float) -> Fill:
    """Create an exit fill and decrement the position quantity."""
    qty = min(qty, pos.qty)
    pnl = (price - pos.entry) * qty * pos.direction
    fee = price * qty * fee_pct / 100.0
    side = "sell" if pos.direction == 1 else "buy"     # exit order side
    pos.qty -= qty
    return Fill(time=time, price=float(price), qty=float(qty), side=side,
                reason=reason, fee=float(fee), pnl=float(pnl))


def check_intrabar_exits(pos: OpenPosition, high: float, low: float,
                         time: Any, fee_pct: float) -> List[Fill]:
    """Check a bar (high/low) against the position's SL / TP1..TP4.

    Conservative fill assumption (documented in the README): if the bar
    touches BOTH the stop-loss and take-profit levels, the stop-loss is
    assumed to have been hit first.
    """
    fills: List[Fill] = []
    if pos is None or pos.qty <= 0:
        return fills

    long = pos.direction == 1
    hit_sl = low <= pos.sl if long else high >= pos.sl
    if hit_sl:
        fills.append(close_fill(pos, pos.qty, pos.sl, time, "SL", fee_pct))
        return fills

    for k, tp in enumerate(pos.tps):
        if pos.tp_hit[k]:
            continue
        tp_touched = high >= tp if long else low <= tp
        if tp_touched:
            qty = pos.initial_qty * TP_FRACTIONS[k]
            fills.append(close_fill(pos, qty, tp, time, f"TP{k + 1}", fee_pct))
            pos.tp_hit[k] = True
    return fills


def unrealized_pnl(pos: Optional[OpenPosition], price: float) -> float:
    if pos is None or pos.qty <= 0:
        return 0.0
    return (price - pos.entry) * pos.qty * pos.direction
