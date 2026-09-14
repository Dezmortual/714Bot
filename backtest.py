# ============================================================
#  714 METHOD — simple offline backtest
#  Runs the strategy over a mock price series (or Alpaca bars if
#  keys are present) and reports simulated equity + trade stats.
#
#  Usage:
#     python backtest.py                     # mock data
#     python backtest.py --symbol AAPL      # Alpaca data (needs keys)
# ============================================================
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from engine.engine import load_config
from engine.strategy import generate_signal, atr
from engine.risk import position_size
from engine.mock_broker import MockBroker
from state import STATE


def run(symbol, cfg, bars_df):
    strat = cfg["strategy"]
    risk = cfg["risk"]
    warmup = cfg["engine"].get("candle_warmup", 60)
    equity = 10_000.0
    wins = losses = 0
    trades = []
    pos = None  # dict(entry, qty, side, stop, target)

    for i in range(warmup, len(bars_df)):
        df = bars_df.iloc[: i + 1]
        close = float(df["close"].iloc[-1])
        sig = generate_signal(df, strat)

        # ---- manage open position ----
        if pos is not None:
            px = close
            hit = False
            if pos["side"] == "buy":
                if px <= pos["stop"]:
                    pnl = (px - pos["entry"]) * pos["qty"]; hit = True
                elif px >= pos["target"]:
                    pnl = (pos["target"] - pos["entry"]) * pos["qty"]; hit = True
            else:
                if px >= pos["stop"]:
                    pnl = (pos["entry"] - px) * pos["qty"]; hit = True
                elif px <= pos["target"]:
                    pnl = (pos["entry"] - pos["target"]) * pos["qty"]; hit = True
            if hit:
                equity += pnl
                if pnl > 0: wins += 1
                else: losses += 1
                trades.append(pnl)
                pos = None

        # ---- enter new ----
        if pos is None and sig:
            entry = sig["entry"]
            stop = sig["stop"]
            qty = position_size(equity, entry, stop, risk)
            if qty > 0:
                tp = risk.get("target_pips", 50) * risk.get("pip_value", 0.01)
                target = entry + tp if sig["side"] == "buy" else entry - tp
                pos = {"entry": entry, "qty": qty, "side": sig["side"],
                       "stop": stop, "target": target}

    print(f"\n=== BACKTEST: {symbol} ===")
    print(f"bars          : {len(bars_df)}")
    print(f"trades        : {len(trades)}")
    print(f"wins / losses : {wins} / {losses}")
    if trades:
        print(f"win rate      : {wins/len(trades)*100:.1f}%")
        print(f"avg pnl       : {sum(trades)/len(trades):.2f}")
    print(f"final equity  : {equity:.2f}  ({(equity-10000)/10000*100:+.1f}%)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="AAPL")
    args = ap.parse_args()

    cfg = load_config(Path(__file__).parent / "config.yaml")
    strat = cfg["strategy"]

    if os.getenv("ALPACA_API_KEY"):
        from engine.broker import Broker
        b = Broker(cfg, STATE)
        df = b.bars(args.symbol, strat["timeframe"], 300)
    else:
        mb = MockBroker(cfg, STATE)
        df = mb.bars(args.symbol, strat["timeframe"], 300)
        print("(using synthetic mock data — set ALPACA keys for real bars)")

    if df is None or df.empty:
        raise SystemExit("no data")

    run(args.symbol, cfg, df)
