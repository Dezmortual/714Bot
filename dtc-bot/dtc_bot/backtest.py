"""
Bar-by-bar backtester for the DTC v1.36 strategy.

Execution model (documented assumptions):
  * Signals are evaluated at bar CLOSE (confirmed bars only) and filled at
    that bar's close price — exactly how the Pine indicator derives its
    entry level (``entry := close``).
  * A signal opposite to an open position closes it and reverses
    (the Pine signal_state machine alternates long/short signals).
  * Intra-bar, if a candle touches both the SL and a TP, the SL is assumed
    first (conservative).
  * Position size = risk_percent of current equity / stop distance,
    optionally levered, with taker fees on every fill.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .strategy import StrategyConfig, generate_signals, make_levels
from .trade import Fill, OpenPosition, check_intrabar_exits, close_fill, unrealized_pnl


@dataclass
class BacktestConfig:
    starting_equity: float = 10_000.0
    risk_percent: float = 1.0     # % of equity risked per trade (via stop distance)
    leverage: float = 1.0         # notional cap = equity * leverage
    fee_pct: float = 0.05         # taker fee per side, %

    @classmethod
    def from_dict(cls, d: dict) -> "BacktestConfig":
        d = d or {}
        return cls(
            starting_equity=float(d.get("starting_equity", 10_000.0)),
            risk_percent=float(d.get("risk_percent", 1.0)),
            leverage=float(d.get("leverage", 1.0)),
            fee_pct=float(d.get("fee_pct", 0.05)),
        )


@dataclass
class BacktestResult:
    trades: List[dict]            # one row per closed position
    fills: List[Fill]
    equity_curve: pd.Series
    stats: Dict

    def summary(self) -> str:
        s = self.stats
        lines = [
            "================  BACKTEST RESULTS  ================",
            f"  Start equity      : {s['starting_equity']:,.2f}",
            f"  End equity        : {s['end_equity']:,.2f}",
            f"  Net PnL           : {s['net_pnl']:+,.2f}  ({s['net_pnl_pct']:+.2f}%)",
            f"  Max drawdown      : {s['max_drawdown_pct']:.2f}%",
            f"  Trades (round)    : {s['n_trades']}",
            f"  Win rate          : {s['win_rate']:.1f}%  ({s['wins']}W / {s['losses']}L)",
            f"  Profit factor     : {s['profit_factor']}",
            f"  Avg R per trade   : {s['avg_r']:+.3f} R",
            f"  Fees paid         : {s['fees_paid']:,.2f}",
            "  Exit breakdown    : "
            + ", ".join(f"{k}={v}" for k, v in s["exit_counts"].items()),
            "=====================================================",
        ]
        return "\n".join(lines)


class Backtester:
    def __init__(self, strat_cfg: StrategyConfig, bt_cfg: BacktestConfig,
                 allow_longs: bool = True, allow_shorts: bool = True):
        self.s = strat_cfg
        self.b = bt_cfg
        self.allow_longs = allow_longs
        self.allow_shorts = allow_shorts
        if bt_cfg.risk_percent <= 0:
            raise ValueError("risk_percent must be > 0")

    # -- sizing ---------------------------------------------------------
    def _size(self, equity: float, entry: float) -> tuple[float, float]:
        stop_dist = entry * self.s.stop_loss_pct / 100.0
        risk_cash = equity * self.b.risk_percent / 100.0
        qty_risk = risk_cash / stop_dist if stop_dist > 0 else 0.0
        qty_cap = (equity * self.b.leverage) / entry
        return min(qty_risk, qty_cap), risk_cash

    def _open(self, direction: int, price: float, time, equity: float,
              fills: List[Fill]) -> Optional[OpenPosition]:
        qty, risk_cash = self._size(equity, price)
        if qty <= 0:
            return None
        levels = make_levels(direction, price, self.s)
        fee = price * qty * self.b.fee_pct / 100.0
        fills.append(Fill(time=time, price=price, qty=qty,
                          side="buy" if direction == 1 else "sell",
                          reason="ENTRY", fee=fee, pnl=0.0))
        return OpenPosition.from_levels(levels, qty, time, fee, risk_cash)

    # -- main loop -------------------------------------------------------
    def run(self, df: pd.DataFrame) -> BacktestResult:
        df = df.dropna(subset=["open", "high", "low", "close"])
        signals = generate_signals(df, self.s)
        warmup = min(self.s.warmup_bars(), max(len(df) - 10, 0))

        equity = self.b.starting_equity
        pos: Optional[OpenPosition] = None
        fills: List[Fill] = []
        trades: List[dict] = []
        curve: Dict = {}

        def settle(current_fills: List[Fill]) -> None:
            """Apply fills to equity."""
            nonlocal equity
            for f in current_fills:
                equity += f.pnl - f.fee

        for i in range(len(df)):
            row = df.iloc[i]
            time = df.index[i]
            sig = signals[i] if i >= max(warmup, 1) else 0

            # 1) intra-bar exits for a position opened on an EARLIER bar
            if pos is not None and pos.entry_time != time:
                new_fills = check_intrabar_exits(
                    pos, float(row["high"]), float(row["low"]), time, self.b.fee_pct
                )
                if new_fills:
                    fills.extend(new_fills)
                    settle(new_fills)
                if pos.qty <= 0:
                    trades.append(_trade_row(pos, fills))
                    pos = None

            # 2) signal at bar close — a fresh signal can re-enter on the
            #    same bar an old position exited (mirrors the live engine)
            if sig != 0:
                allowed = (sig == 1 and self.allow_longs) or \
                          (sig == -1 and self.allow_shorts)
                if pos is not None and pos.direction != sig:
                    # close & reverse at the signal close (signal_state alternation)
                    f = close_fill(pos, pos.qty, float(row["close"]), time,
                                   "REVERSAL", self.b.fee_pct)
                    fills.append(f)
                    settle([f])
                    trades.append(_trade_row(pos, fills))
                    pos = None
                if pos is None and allowed:
                    pos = self._open(sig, float(row["close"]), time, equity, fills)
                    if pos is not None:
                        equity -= pos.entry_fee      # entry fee

            # 3) mark-to-market
            curve[time] = equity + unrealized_pnl(pos, float(row["close"]))

        # end-of-data liquidation (mark only, not counted as a strategy exit)
        if pos is not None:
            f = close_fill(pos, pos.qty, float(df.iloc[-1]["close"]),
                           df.index[-1], "EOD", self.b.fee_pct)
            fills.append(f)
            settle([f])
            trades.append(_trade_row(pos, fills))
            pos = None
            curve[df.index[-1]] = equity

        equity_curve = pd.Series(curve, name="equity")
        stats = _compute_stats(trades, fills, equity_curve, self.b.starting_equity)
        return BacktestResult(trades=trades, fills=fills,
                              equity_curve=equity_curve, stats=stats)


# ----------------------------------------------------------------------
# stats helpers
# ----------------------------------------------------------------------

def _trade_row(pos: OpenPosition, all_fills: List[Fill]) -> dict:
    """Summarise one closed position from its fills."""
    exit_fills = [f for f in all_fills if f.reason != "ENTRY"
                  and f.time >= pos.entry_time]
    pnl = sum(f.pnl for f in exit_fills)
    fees = pos.entry_fee + sum(f.fee for f in exit_fills)
    net = pnl - fees
    last = exit_fills[-1] if exit_fills else None
    tp_hits = sum(1 for f in exit_fills if f.reason.startswith("TP"))
    return {
        "side": pos.side,
        "entry_time": str(pos.entry_time),
        "entry": pos.entry,
        "exit_time": str(last.time) if last else "",
        "exit_reason": last.reason if last else "",
        "qty": pos.initial_qty,
        "gross_pnl": pnl,
        "fees": fees,
        "net_pnl": net,
        "r_multiple": net / pos.risk_cash if pos.risk_cash else 0.0,
        "tp_hits": tp_hits,
        "sl": pos.sl,
        "tps": ",".join(f"{t:g}" for t in pos.tps),
    }


def _compute_stats(trades: List[dict], fills: List[Fill],
                   equity_curve: pd.Series, starting_equity: float) -> Dict:
    n = len(trades)
    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    losses = n - wins
    gross_profit = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
    gross_loss = -sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else 0.0)
    end_equity = float(equity_curve.iloc[-1]) if len(equity_curve) else starting_equity

    if len(equity_curve):
        roll_max = equity_curve.cummax()
        dd = (equity_curve - roll_max) / roll_max * 100.0
        max_dd = float(dd.min())
    else:
        max_dd = 0.0

    exit_counts: Dict[str, int] = {}
    for t in trades:
        r = "TP" if t["exit_reason"].startswith("TP") else t["exit_reason"] or "?"
        exit_counts[r] = exit_counts.get(r, 0) + 1

    return {
        "starting_equity": starting_equity,
        "end_equity": end_equity,
        "net_pnl": end_equity - starting_equity,
        "net_pnl_pct": (end_equity - starting_equity) / starting_equity * 100.0,
        "max_drawdown_pct": abs(max_dd),
        "n_trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / n * 100.0) if n else 0.0,
        "profit_factor": (f"{profit_factor:.2f}" if np.isfinite(profit_factor)
                          else "inf"),
        "avg_r": float(np.mean([t["r_multiple"] for t in trades])) if n else 0.0,
        "fees_paid": sum(f.fee for f in fills),
        "exit_counts": exit_counts,
    }
