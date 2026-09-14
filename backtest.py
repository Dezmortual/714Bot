# ============================================================
#  714 METHOD — backtest engine with charts
#  Replays the strategy over historical bars using the SAME
#  entry/exit logic as the live engine:
#    * same sliding candle window (engine.candle_warmup)
#    * W/M formation signals, ATR stop floor, R:R target
#    * breakeven move, partial profit + locked stop
#    * signal cooldown + re-entry cooldown (converted from
#      real-time seconds to candle units)
#    * crypto buy-only (can't short on Alpaca)
#    * scan/enter happens BEFORE manage(), like the live tick
#  Performance: signals are computed on the fixed-size warmup window
#  (like live) and ATR comes from a prefix sum, so 1000+ bars
#  backtest in well under a second.
#
#  Output:
#    * stats (equity, win rate, profit factor, max DD, Sharpe,
#      expectancy in R, ...)
#    * mark-to-market equity curve
#    * trade list (with partial/exit events)
#    * PNG chart: candles + trade markers + initial SL/TP,
#      equity curve, drawdown panel
#
#  CLI:
#     python backtest.py                          # mock data, AAPL
#     python backtest.py --symbol BTC/USD --limit 500
#     python backtest.py --symbol AAPL --no-chart
#
#  Web:  POST /api/backtest  (page on /backtest)
# ============================================================
import argparse
import math
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import numpy as np
import pandas as pd

from engine.engine import load_config
from engine.strategy import generate_signal
from engine.risk import position_size
from state import STATE

INITIAL_EQUITY = 10_000.0

# Dashboard-matching dark theme for the charts
C = {
    "bg": "#0b1220", "panel": "#111a2c", "line": "#1e2a44",
    "txt": "#e6edf7", "dim": "#8fa3c0", "green": "#22c55e",
    "red": "#ef4444", "blue": "#3b82f6", "amber": "#f59e0b",
}


# ============================================================
#  Data fetching
# ============================================================
def fetch_bars(symbol, cfg, limit):
    """Return (bars_df, source). Uses Alpaca when keys are present,
    otherwise the synthetic mock series (clearly labelled)."""
    tf = cfg["strategy"]["timeframe"]
    if os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"):
        from engine.broker import Broker
        b = Broker(cfg, STATE)
        df = b.bars(symbol, tf, limit)
        return df, "alpaca"
    from engine.mock_broker import MockBroker
    mb = MockBroker(cfg, STATE)
    df = mb.bars(symbol, tf, limit)
    if df is not None and not df.empty:
        # The mock series starts at "now" and advances forward in time;
        # shift the timestamps back so the LAST bar lands at now (a
        # backtest window should end in the present, not the future).
        _dt = pd.Series(df.index).diff().dropna()
        if len(_dt):
            shift = _dt.median() * (len(df) - 1)
            df = df.copy()
            df.index = df.index - pd.Timedelta(seconds=shift.total_seconds())
    return df, "simulation"


# ============================================================
#  Backtest core (mirrors Engine.scan/enter/manage)
# ============================================================
def run_backtest(symbol, cfg, bars_df, initial_equity=INITIAL_EQUITY):
    """Run the 714 strategy over `bars_df` and return
    {stats, trades, equity: [(ts, value)], initial_equity}."""
    strat = cfg["strategy"]
    risk = cfg["risk"]
    eng = cfg["engine"]
    warmup = int(eng.get("candle_warmup", 60))
    reentry_secs = eng.get("reentry_cooldown_secs", 300)
    signal_cooldown_secs = eng.get("signal_cooldown_secs", 300)
    atr_period = int(risk.get("atr_period", 14))
    atr_mult = risk.get("atr_stop_mult", 2.0)
    min_stop_pct = risk.get("min_stop_pct", 0.003)
    rr = risk.get("risk_reward_ratio", 2.0)
    be_r = risk.get("breakeven_r", 0.5)
    pp_r = risk.get("partial_r", 1.0)
    partial_frac = risk.get("partial_fraction", 0.5)
    lock_r = risk.get("lock_r", 0.5)

    n = len(bars_df)
    if n <= warmup:
        raise ValueError(f"not enough bars ({n}); need > {warmup}")

    # The live engine's cooldowns are REAL-time seconds (the bot polls
    # every ~15s). In a bar-by-bar replay each step is one whole candle,
    # so convert them to candle units (300s -> 0 candles on 15Min bars,
    # -> 5 candles on 1Min bars). Same-bar re-entry right after an exit
    # is already prevented structurally (in live, manage() runs after
    # the scan, so an entry can never happen on the bar it exited).
    _dt = np.diff(bars_df.index.to_series().astype("int64").values)
    _dt = _dt[_dt > 0]
    bar_secs = float(np.median(_dt)) / 1e9 if len(_dt) else 900.0
    reentry_bars = max(0, int(round(reentry_secs / bar_secs)))
    signal_cooldown_bars = max(0, int(round(signal_cooldown_secs / bar_secs)))

    is_crypto = "/" in symbol
    equity = float(initial_equity)
    pos = None            # open position dict
    trades = []           # completed round trips
    equity_curve = []     # (ts, mark-to-market equity)
    last_fire = None      # (side, bar_idx) of last signal fire
    last_exit_bar = None  # bar_idx of last trade exit

    highs = bars_df["high"].values.astype(float)
    lows = bars_df["low"].values.astype(float)
    closes = bars_df["close"].values.astype(float)
    idx = bars_df.index

    # ATR precompute: the strategy's atr() is a SIMPLE rolling mean of TR,
    # so a prefix sum gives the exact per-bar value in O(1).
    prev_close = np.empty(n)
    prev_close[0] = closes[0]
    prev_close[1:] = closes[:-1]
    tr = np.maximum(highs - lows,
                    np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close)))
    tr_cum = np.concatenate([[0.0], np.cumsum(tr)])

    for i in range(warmup, n):
        ts = idx[i]
        close = float(closes[i])

        atr_val = ((tr_cum[i + 1] - tr_cum[i + 1 - atr_period]) / atr_period
                   if i + 1 >= atr_period else 0.0)

        # ---- signal: exactly what the live engine computes —
        # generate_signal() on the most recent `warmup` bars ----
        sig = generate_signal(bars_df.iloc[i + 1 - warmup: i + 1], strat)

        # ---- entry gates (live: enter() during the scan, before manage) ----
        if sig is not None:
            side = sig["side"]
            # crypto can't be shorted on Alpaca — mirror live skip
            crypto_skip = is_crypto and side == "sell"
            in_cooldown = (last_fire is not None and last_fire[0] == side
                           and (i - last_fire[1]) <= signal_cooldown_bars)
            if not crypto_skip and not in_cooldown:
                last_fire = (side, i)
                reentry_ok = (last_exit_bar is None
                              or (i - last_exit_bar) > reentry_bars)
                if pos is None and reentry_ok:
                    entry = float(sig["entry"])
                    swing_dist = abs(entry - sig["stop"])
                    stop_dist = max(swing_dist, atr_val * atr_mult,
                                    entry * min_stop_pct)
                    stop = entry - stop_dist if side == "buy" else entry + stop_dist
                    target = entry + stop_dist * rr if side == "buy" else entry - stop_dist * rr
                    qty = position_size(equity, entry, stop, risk)
                    if qty > 0 and entry > 0 and swing_dist > 0:
                        pos = {
                            "entry": entry, "qty": qty, "qty_left": qty,
                            "side": side, "stop": stop, "target": target,
                            "init_stop": stop, "init_target": target,
                            "stop_dist": stop_dist, "lock": None,
                            "be_done": False, "partial_done": False,
                            "entry_i": i, "entry_ts": ts,
                            "type": sig.get("type", ""), "reason": sig.get("reason", ""),
                            "realized": 0.0, "exits": [],
                        }

        # ---- manage open position (same order as Engine.manage) ----
        if pos is not None:
            entry, side = pos["entry"], pos["side"]
            stop_dist = pos["stop_dist"] or abs(entry - pos["stop"])
            if stop_dist <= 0:
                stop_dist = 1e-9

            exit_px = None
            exit_reason = None

            # 1) stop (initial or moved to breakeven)
            if (side == "buy" and close <= pos["stop"]) or (side == "sell" and close >= pos["stop"]):
                exit_px, exit_reason = close, "stop loss"

            # 2) move stop to breakeven at +be_r R
            move_r = ((close - entry) if side == "buy" else (entry - close)) / stop_dist
            if exit_px is None:
                if not pos["be_done"] and move_r >= be_r:
                    pos["stop"] = entry
                    pos["be_done"] = True

                # 3) partial profit at +pp_r R, lock SL at +lock_r R
                if not pos["partial_done"] and move_r >= pp_r:
                    pos["partial_done"] = True
                    pos["lock"] = entry + lock_r * stop_dist if side == "buy" else entry - lock_r * stop_dist
                    q = pos["qty_left"] * partial_frac
                    pnl = (close - entry) * q if side == "buy" else (entry - close) * q
                    equity += pnl
                    pos["realized"] += pnl
                    pos["qty_left"] -= q
                    pos["exits"].append(
                        {"ts": ts, "px": close, "qty": q, "pnl": pnl,
                         "reason": f"partial +{pp_r}R"})

                # 4) locked stop after partial
                if pos["lock"] is not None and (
                        (side == "buy" and close <= pos["lock"])
                        or (side == "sell" and close >= pos["lock"])):
                    exit_px, exit_reason = close, "locked stop"

            # 5) take profit
            if exit_px is None and (
                    (side == "buy" and close >= pos["target"])
                    or (side == "sell" and close <= pos["target"])):
                exit_px, exit_reason = close, "take profit"

            if exit_px is not None and pos["qty_left"] > 1e-12:
                q_final = pos["qty_left"]
                pnl = (exit_px - entry) * q_final if side == "buy" else (entry - exit_px) * q_final
                equity += pnl
                pos["realized"] += pnl
                pos["qty_left"] = 0.0
                pos["exits"].append(
                    {"ts": ts, "px": exit_px, "qty": q_final, "pnl": pnl, "reason": exit_reason})
                trades.append(_finish_trade(pos, ts, exit_px))
                last_exit_bar = i
                pos = None

        # ---- mark-to-market equity ----
        eq_mark = equity
        if pos is not None:
            eq_mark += ((close - pos["entry"]) * pos["qty_left"] if pos["side"] == "buy"
                        else (pos["entry"] - close) * pos["qty_left"])
        equity_curve.append((ts, eq_mark))

    # Force-close anything still open at the end of the data so the
    # report is complete (flagged as "end of data").
    if pos is not None:
        close = float(closes[-1])
        ts = idx[-1]
        pnl = (close - pos["entry"]) * pos["qty_left"] if pos["side"] == "buy" else (pos["entry"] - close) * pos["qty_left"]
        equity += pnl
        pos["realized"] += pnl
        pos["exits"].append({"ts": ts, "px": close, "qty": pos["qty_left"], "pnl": pnl,
                             "reason": "end of data"})
        trades.append(_finish_trade(pos, ts, close, forced=True))
        equity_curve[-1] = (ts, equity)

    stats = _compute_stats(symbol, source=None, bars_df=bars_df, trades=trades,
                           equity_curve=equity_curve, initial_equity=initial_equity)
    return {
        "stats": stats,
        "trades": trades,
        "equity": equity_curve,
        "initial_equity": initial_equity,
    }


def _finish_trade(pos, exit_ts, exit_px, forced=False):
    entry, qty = pos["entry"], pos["qty"]
    risk_amount = pos["stop_dist"] * qty
    pnl = pos["realized"]
    r = pnl / risk_amount if risk_amount > 0 else 0.0
    try:
        n_bars = int((exit_ts - pos["entry_ts"]).total_seconds() // 60)
    except Exception:
        n_bars = 0
    return {
        "side": pos["side"],
        "type": pos["type"],
        "reason": pos["reason"],
        "entry": round(entry, 6),
        "exit": round(exit_px, 6),
        "qty": round(qty, 6),
        "initial_stop": round(pos["init_stop"], 6),
        "initial_target": round(pos["init_target"], 6),
        "entry_ts": _iso(pos["entry_ts"]),
        "exit_ts": _iso(exit_ts),
        "bars_held": n_bars,
        "pnl": round(pnl, 2),
        "r": round(r, 2),
        "win": pnl > 0,
        "partial": pos["partial_done"],
        "breakeven": pos["be_done"],
        "forced": forced,
        "exits": [{"ts": _iso(e["ts"]), "px": round(e["px"], 6),
                   "qty": round(e["qty"], 6), "pnl": round(e["pnl"], 2),
                   "reason": e["reason"]} for e in pos["exits"]],
    }


def _iso(ts):
    try:
        return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _compute_stats(symbol, source, bars_df, trades, equity_curve, initial_equity):
    eq = pd.Series([v for _, v in equity_curve], index=[t for t, _ in equity_curve], dtype=float)
    final_equity = float(eq.iloc[-1]) if len(eq) else initial_equity
    ret_pct = (final_equity / initial_equity - 1) * 100

    peak = eq.cummax()
    dd = (eq - peak) / peak * 100 if len(eq) else pd.Series(dtype=float)
    max_dd = float(dd.min()) if len(dd) else 0.0

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    else:
        profit_factor = float("inf") if gross_win > 0 else 0.0

    rets = eq.pct_change().dropna()
    span_days = max((bars_df.index[-1] - bars_df.index[0]).total_seconds() / 86400, 1e-9)
    bars_per_year = max(len(eq) / span_days * 365, 1.0)
    sharpe = (float(rets.mean() / rets.std() * math.sqrt(bars_per_year))
              if len(rets) > 2 and rets.std() > 0 else 0.0)

    avg_r = float(np.mean([t["r"] for t in trades])) if trades else 0.0
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0
    best = max(trades, key=lambda t: t["pnl"]) if trades else None
    worst = min(trades, key=lambda t: t["pnl"]) if trades else None

    return {
        "symbol": symbol,
        "source": source,
        "bars": int(len(bars_df)),
        "period_start": _iso(bars_df.index[0]),
        "period_end": _iso(bars_df.index[-1]),
        "initial_equity": round(initial_equity, 2),
        "final_equity": round(final_equity, 2),
        "return_pct": round(ret_pct, 2),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "profit_factor": round(profit_factor, 2) if math.isfinite(profit_factor) else 99.0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_r": round(avg_r, 2),
        "best_trade": round(best["pnl"], 2) if best else 0.0,
        "worst_trade": round(worst["pnl"], 2) if worst else 0.0,
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
    }


# ============================================================
#  Chart rendering (matplotlib, dark theme matching the dashboard)
# ============================================================
def render_chart(symbol, source, bars_df, result, path):
    """Save a 3-panel PNG (candles+trades / equity / drawdown) to path."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.lines import Line2D
    from matplotlib.ticker import ScalarFormatter

    trades = result["trades"]
    eq_ts = [t for t, _ in result["equity"]]
    eq_vals = [v for _, v in result["equity"]]
    stats = result["stats"]

    o = bars_df["open"].values.astype(float)
    h = bars_df["high"].values.astype(float)
    l = bars_df["low"].values.astype(float)
    c = bars_df["close"].values.astype(float)
    dt_num = mdates.date2num([pd.Timestamp(t).to_pydatetime() for t in bars_df.index])

    # median bar spacing (in days) -> candle body width
    diffs = np.diff(dt_num)
    diffs = diffs[diffs > 0]
    median_dt = float(np.median(diffs)) if len(diffs) else 1 / 96.0
    width = max(median_dt * 0.7, 1e-6)

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(15, 9), sharex=True,
        gridspec_kw={"height_ratios": [2.5, 1.05, 0.75], "hspace": 0.07},
    )
    fig.patch.set_facecolor(C["bg"])
    for ax in (ax1, ax2, ax3):
        ax.set_facecolor(C["panel"])
        ax.tick_params(colors=C["dim"], labelsize=9)
        for s in ax.spines.values():
            s.set_color(C["line"])
        ax.grid(True, color=C["line"], linewidth=0.6, alpha=0.45)
        ax.set_axisbelow(True)

    # ---- panel 1: candles + trade markers + initial SL/TP ----
    up = c >= o
    body = np.abs(c - o)
    rng = (h.max() - l.min()) if len(h) else 1.0
    min_body = rng * 0.004 if rng > 0 else 1e-9
    body_plot = np.where(body < min_body, min_body, body)
    bottom = np.minimum(o, c)

    ax1.bar(dt_num[up], body_plot[up], bottom=bottom[up], width=width,
            color=C["green"], linewidth=0, zorder=3)
    ax1.bar(dt_num[~up], body_plot[~up], bottom=bottom[~up], width=width,
            color=C["red"], linewidth=0, zorder=3)
    ax1.vlines(dt_num[up], l[up], h[up], color=C["green"], linewidth=0.5, zorder=2)
    ax1.vlines(dt_num[~up], l[~up], h[~up], color=C["red"], linewidth=0.5, zorder=2)

    for t in trades:
        ex = mdates.date2num(pd.Timestamp(t["entry_ts"]).to_pydatetime())
        xx = mdates.date2num(pd.Timestamp(t["exit_ts"]).to_pydatetime())
        buy = t["side"] == "buy"
        mc = C["green"] if buy else C["red"]
        mk = "^" if buy else "v"
        ax1.scatter([ex], [t["entry"]], marker=mk, s=85, color=mc,
                    edgecolors=C["bg"], linewidths=0.6, zorder=6)
        ax1.scatter([xx], [t["exit"]], marker="x", s=45, color=C["amber"],
                    linewidths=0.6, zorder=6)
        # initial stop / target as dashed segments across the trade
        ax1.hlines(t["initial_stop"], ex, xx, colors=C["red"],
                   linestyles="--", linewidth=0.7, alpha=0.75, zorder=4)
        ax1.hlines(t["initial_target"], ex, xx, colors=C["green"],
                   linestyles="--", linewidth=0.7, alpha=0.75, zorder=4)

    pf = stats["profit_factor"]
    pf_txt = f"{pf:.2f}" if pf < 99 else "∞"
    anno = (f"return {stats['return_pct']:+.1f}%   trades {stats['trades']}   "
            f"win rate {stats['win_rate']}%   PF {pf_txt}   "
            f"max DD {stats['max_drawdown_pct']:.1f}%")
    ax1.text(0.01, 0.97, anno, transform=ax1.transAxes, ha="left", va="top",
             fontsize=10, color=C["txt"],
             bbox=dict(boxstyle="round,pad=0.4", facecolor=C["bg"],
                       edgecolor=C["line"], alpha=0.85))
    ax1.set_ylabel("price", color=C["dim"], fontsize=9)

    legend_handles = [
        Line2D([], [], color=C["green"], marker="^", ls="", label="buy entry"),
        Line2D([], [], color=C["red"], marker="v", ls="", label="sell entry"),
        Line2D([], [], color=C["amber"], marker="x", ls="", label="exit"),
        Line2D([], [], color=C["red"], ls="--", label="initial stop"),
        Line2D([], [], color=C["green"], ls="--", label="initial target"),
    ]
    ax1.legend(handles=legend_handles, loc="upper right", fontsize=8,
               facecolor=C["bg"], edgecolor=C["line"], labelcolor=C["dim"],
               framealpha=0.9, ncol=5)

    # ---- panel 2: equity curve ----
    eq_arr = np.asarray(eq_vals, dtype=float)
    peak = np.maximum.accumulate(eq_arr)
    ax2.plot(eq_ts, eq_vals, color=C["blue"], linewidth=1.4, zorder=4)
    ax2.fill_between(eq_ts, eq_vals, eq_arr.min() - (eq_arr.max() - eq_arr.min()) * 0.08,
                     color=C["blue"], alpha=0.10, zorder=3)
    ax2.axhline(result["initial_equity"], color=C["dim"], linewidth=0.8,
                linestyle=":", alpha=0.7)
    # explicit y-range + no scientific offset (matplotlib would otherwise
    # label the axis "5 / 0 / -5" with a "+1e4" superscript)
    ax2.yaxis.set_major_formatter(ScalarFormatter(useOffset=False))
    lo, hi = float(eq_arr.min()), float(eq_arr.max())
    pad = max((hi - lo) * 0.25, hi * 0.0005, 0.5)
    ax2.set_ylim(lo - pad, hi + pad)
    col = C["green"] if eq_vals[-1] >= result["initial_equity"] else C["red"]
    ax2.text(0.01, 0.90, f"final ${eq_vals[-1]:,.2f}  ({stats['return_pct']:+.1f}%)",
             transform=ax2.transAxes, ha="left", va="top", fontsize=10, color=col,
             fontweight="bold")
    ax2.set_ylabel("equity", color=C["dim"], fontsize=9)

    # ---- panel 3: drawdown (%) ----
    dd_pct = (eq_arr - peak) / peak * 100
    ax3.fill_between(eq_ts, dd_pct, 0, color=C["red"], alpha=0.45, zorder=3)
    ax3.plot(eq_ts, dd_pct, color=C["red"], linewidth=0.9, zorder=4)
    ax3.set_ylabel("drawdown %", color=C["dim"], fontsize=9)
    worst_i = int(np.argmin(dd_pct))
    ax3.scatter([eq_ts[worst_i]], [dd_pct[worst_i]], s=22, color=C["red"], zorder=5)
    ax3.annotate(f" {dd_pct[worst_i]:.1f}%", xy=(eq_ts[worst_i], dd_pct[worst_i]),
                 fontsize=8, color=C["txt"])

    # ---- x-axis formatting + title ----
    ax3.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b %d, %Y"))
    fig.autofmt_xdate(rotation=0, ha="center")
    src_note = "synthetic data" if source == "simulation" else "Alpaca data"
    fig.suptitle(f"714 Method backtest — {symbol}   "
                 f"({stats['period_start']} → {stats['period_end']}, {src_note})",
                 color=C["txt"], fontsize=13, fontweight="bold")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return out


# ============================================================
#  CLI
# ============================================================
def _print_stats(symbol, stats, chart_path=None):
    print(f"\n=== BACKTEST: {symbol} ===")
    if stats.get("source"):
        print(f"data source   : {stats['source']}")
    print(f"period        : {stats['period_start']}  →  {stats['period_end']}")
    print(f"bars          : {stats['bars']}")
    print(f"trades        : {stats['trades']}   (wins {stats['wins']} / losses {stats['losses']})")
    print(f"win rate      : {stats['win_rate']:.1f}%")
    print(f"profit factor : {stats['profit_factor']:.2f}")
    print(f"avg win/loss  : {stats['avg_win']:+.2f} / {stats['avg_loss']:+.2f}   "
          f"avg R {stats['avg_r']:+.2f}")
    print(f"best / worst  : {stats['best_trade']:+.2f} / {stats['worst_trade']:+.2f}")
    print(f"max drawdown  : {stats['max_drawdown_pct']:.2f}%")
    print(f"sharpe        : {stats['sharpe']:.2f}")
    print(f"final equity  : {stats['final_equity']:,.2f}  ({stats['return_pct']:+.2f}%)")
    if chart_path:
        print(f"chart         : {chart_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="714 Method backtest with chart")
    ap.add_argument("--symbol", default="AAPL", help="ticker, e.g. AAPL or BTC/USD")
    ap.add_argument("--limit", type=int, default=500, help="number of bars (default 500)")
    ap.add_argument("--no-chart", action="store_true", help="skip PNG chart generation")
    args = ap.parse_args()

    cfg = load_config(Path(__file__).parent / "config.yaml")
    symbol = args.symbol.upper()
    limit = max(100, min(args.limit, 3000))

    print(f"fetching {limit} bars for {symbol} ...")
    df, source = fetch_bars(symbol, cfg, limit)
    if source == "simulation":
        print("(using synthetic mock data — set ALPACA keys for real bars)")
    if df is None or df.empty:
        raise SystemExit("no data")

    t0 = time.time()
    result = run_backtest(symbol, cfg, df)
    stats = result["stats"]
    stats["source"] = source
    _print_stats(symbol, stats)

    if not args.no_chart:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", symbol)
        out = Path(__file__).parent / "data" / f"backtest_{safe}.png"
        render_chart(symbol, source, df, result, out)
        print(f"chart         : {out}")
    print(f"done in {time.time() - t0:.1f}s")
