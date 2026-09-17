# ============================================================
#  DTC v1.36 — Offline Backtest Engine
#  Simulates DTC v1.36 6-EMA ribbon strategy with multi-stage TP
#  (TP1 breakeven, TP2/TP3 trailing, TP4 target).
#
#  Usage:
#     python backtest.py                     # runs simulation data
#     python backtest.py --symbol AAPL      # runs Alpaca data (with keys)
# ============================================================
import argparse
import os
from pathlib import Path
import pandas as pd

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from engine.engine import load_config
from engine.strategy import compute_dtc_signals
from engine.risk import position_size
from engine.mock_broker import MockBroker
from state import STATE


def run_backtest(symbol, cfg, df):
    strat = cfg["strategy"]
    risk = cfg["risk"]
    warmup = cfg["engine"].get("candle_warmup", 90)

    df_sig = compute_dtc_signals(df, strat)
    equity = 10_000.0
    wins = 0
    losses = 0
    trades = []
    pos = None

    sl_pct = float(strat.get("stop_loss_pct", 0.25)) / 100.0
    tp1_mult = float(strat.get("tp1_multiplier", 1.0))
    tp2_mult = float(strat.get("tp2_multiplier", 2.0))
    tp3_mult = float(strat.get("tp3_multiplier", 3.0))
    tp4_mult = float(strat.get("tp4_multiplier", 4.0))

    min_warmup = max(cfg.get("len6", 60) + 10, cfg.get("atr_period", 14) + 10)
    for i in range(min_warmup, len(df_sig)):
        row = df_sig.iloc[i]
        px = float(row["close"])
        sig = row["dtc_signal"]
        if pd.isna(sig) or sig not in ("buy", "sell"):
            sig = None

        # 1. Manage active position
        if pos is not None:
            side = pos["side"]
            entry = pos["entry"]
            stop = pos["stop"]
            qty = pos["qty"]

            # Stop loss hit
            hit_sl = (side == "buy" and px <= stop) or (side == "sell" and px >= stop)
            if hit_sl:
                pnl = (px - entry) * qty if side == "buy" else (entry - px) * qty
                equity += pnl
                trades.append(pnl)
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                pos = None
            else:
                # Check TP targets
                dist = pos["dist"]
                tp1 = entry + dist * tp1_mult if side == "buy" else entry - dist * tp1_mult
                tp2 = entry + dist * tp2_mult if side == "buy" else entry - dist * tp2_mult
                tp4 = entry + dist * tp4_mult if side == "buy" else entry - dist * tp4_mult

                # Trailing logic
                if (side == "buy" and px >= tp1) or (side == "sell" and px <= tp1):
                    pos["stop"] = entry  # Breakeven
                if (side == "buy" and px >= tp2) or (side == "sell" and px <= tp2):
                    pos["stop"] = tp1    # Trail to TP1

                # Final target hit
                if (side == "buy" and px >= tp4) or (side == "sell" and px <= tp4):
                    pnl = (px - entry) * qty if side == "buy" else (entry - px) * qty
                    equity += pnl
                    trades.append(pnl)
                    wins += 1
                    pos = None

        # 2. Enter new position
        if pos is None and sig:
            entry = px
            dist = entry * sl_pct
            stop = entry - dist if sig == "buy" else entry + dist
            qty = position_size(equity, entry, stop, risk)
            if qty > 0:
                pos = {
                    "side": sig,
                    "entry": entry,
                    "stop": stop,
                    "dist": dist,
                    "qty": qty,
                }

    print(f"\n==========================================")
    print(f"📊 DTC v1.36 BACKTEST RESULT: {symbol}")
    print(f"==========================================")
    print(f"Candles analyzed : {len(df_sig)}")
    print(f"Total trades     : {len(trades)}")
    print(f"Wins / Losses    : {wins} / {losses}")
    if trades:
        win_rate = (wins / len(trades)) * 100
        print(f"Win Rate         : {win_rate:.1f}%")
        print(f"Average Trade PnL: ${sum(trades) / len(trades):.2f}")
        print(f"Max Profit Trade : ${max(trades):.2f}")
        print(f"Max Loss Trade   : ${min(trades):.2f}")
    print(f"Final Equity     : ${equity:.2f} ({(equity - 10000) / 10000 * 100:+.2f}%)")
    print(f"==========================================\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USD")
    args = ap.parse_args()

    cfg = load_config(Path(__file__).parent / "config.yaml")
    strat = cfg["strategy"]

    if os.getenv("ALPACA_API_KEY"):
        from engine.broker import Broker
        b = Broker(cfg, STATE)
        df = b.bars(args.symbol, strat["timeframe"], 500)
    else:
        mb = MockBroker(cfg, STATE)
        df = mb.bars(args.symbol, strat["timeframe"], 500)
        print("(using synthetic mock data — configure Alpaca keys in .env for live broker bars)")

    if df is None or df.empty:
        raise SystemExit("No bar data received")

    run_backtest(args.symbol, cfg, df)
