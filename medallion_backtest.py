# ============================================================
#  MEDALLION METHOD — offline backtest (mock or Alpaca data)
#  Simulates the mean-reversion + pairs system bar-by-bar with
#  stops, mean-exits, time stops and a simple cost model.
#
#  Usage:
#     python medallion_backtest.py                 # mock data, all symbols+pairs
#     python medallion_backtest.py --symbol AAPL  # single symbol, real bars if keys set
# ============================================================
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from engine.engine import load_config
from engine import medallion as med
from engine.risk import position_size
from engine.mock_broker import MockBroker
from state import STATE

COST_BPS = 2.0  # 2 bps per side (commission + slippage estimate)


def _cost(notional):
    return abs(notional) * COST_BPS / 10_000.0


# ------------------------------------------------------------
def backtest_single(symbol, cfg, df, capital):
    """Mean-reversion (+optional breakout) backtest for one symbol."""
    mcfg, risk = cfg["medallion"], cfg["risk"]
    need = max(mcfg.get("mr_lookback", 20), mcfg.get("trend_len", 50),
               mcfg.get("breakout_len", 20)) + 5
    max_hold = mcfg.get("max_hold_bars", 20) or 10 ** 9
    rr = risk.get("risk_reward_ratio", 2.0)

    equity = capital
    pos = None
    cooldown = 0
    wins = losses = 0
    pnls = []

    for i in range(need, len(df)):
        window = df.iloc[:i + 1]
        px = float(window["close"].iloc[-1])

        if pos is not None:
            pos["bars"] += 1
            exit_px, reason = None, ""
            if pos["side"] == "buy":
                if px <= pos["stop"]:
                    exit_px, reason = pos["stop"], "stop"
                elif px >= pos["target"]:
                    exit_px, reason = pos["target"], "mean"
            else:
                if px >= pos["stop"]:
                    exit_px, reason = pos["stop"], "stop"
                elif px <= pos["target"]:
                    exit_px, reason = pos["target"], "mean"
            if exit_px is None and pos["bars"] >= max_hold:
                exit_px, reason = px, "time"
            if exit_px is not None:
                gross = ((exit_px - pos["entry"]) * pos["qty"]
                         if pos["side"] == "buy"
                         else (pos["entry"] - exit_px) * pos["qty"])
                net = gross - _cost(pos["qty"] * pos["entry"]) - _cost(pos["qty"] * exit_px)
                equity += net
                pnls.append(net)
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                pos = None
                cooldown = 2

        if cooldown > 0:
            cooldown -= 1
        if pos is None and cooldown <= 0:
            sig = med.generate_signal(window, mcfg)
            if sig:
                if "/" in symbol and sig["side"] == "sell":
                    continue  # Alpaca can't short crypto
                entry = px
                stop = float(sig["stop"])
                tgt = float(sig.get("target_override") or 0) or None
                if tgt is None:
                    tgt = entry + abs(entry - stop) * rr if sig["side"] == "buy" \
                        else entry - abs(entry - stop) * rr
                qty = position_size(equity, entry, stop, risk)
                if qty > 0:
                    pos = {"side": sig["side"], "entry": entry, "qty": qty,
                           "stop": stop, "target": tgt, "bars": 0}

    return {"symbol": symbol, "trades": len(pnls), "wins": wins,
            "losses": losses, "pnl": round(equity - capital, 2),
            "equity": round(equity, 2)}


# ------------------------------------------------------------
def backtest_pair(a, b, cfg, df_a, df_b, capital):
    """Hedged pairs backtest: short winner + long loser, exit together."""
    mcfg, risk = cfg["medallion"], cfg["risk"]
    look = mcfg.get("pair_lookback", 30)
    need = look + 5
    entry_z = mcfg.get("pair_entry_z", 2.0)
    exit_z = mcfg.get("pair_exit_z", 0.5)
    max_hold = (mcfg.get("max_hold_bars", 20) or 10 ** 9) * 2  # spreads need room
    atr_mult = mcfg.get("atr_stop_mult", 2.0)
    min_pct = mcfg.get("min_stop_pct", 0.003)
    risk_pct = risk.get("risk_percent", 1.0)

    n = min(len(df_a), len(df_b))
    ca, cb = df_a["close"].iloc[-n:].reset_index(drop=True), df_b["close"].iloc[-n:].reset_index(drop=True)

    equity = capital
    pos = None
    cooldown = 0
    wins = losses = 0
    pnls = []

    def close_legs(i, reason):
        nonlocal equity, pos, wins, losses
        pxa = float(ca.iloc[i])
        pxb = float(cb.iloc[i])
        la, lb = pos["legs"]
        # exits happen at market, unless a leg's stop was breached
        exa, exb = pxa, pxb
        if reason == "stopA":
            exa = la["stop"]
        if reason == "stopB":
            exb = lb["stop"]
        pa = (exa - la["entry"]) * la["qty"] if la["side"] == "buy" else (la["entry"] - exa) * la["qty"]
        pb = (exb - lb["entry"]) * lb["qty"] if lb["side"] == "buy" else (lb["entry"] - exb) * lb["qty"]
        costs = sum(_cost(leg["qty"] * leg["entry"]) + _cost(leg["qty"] * ex)
                    for leg, ex in ((la, exa), (lb, exb)))
        net = pa + pb - costs
        equity += net
        pnls.append(net)
        if net > 0:
            wins += 1
        else:
            losses += 1
        pos = None

    for i in range(need, n):
        if pos is not None:
            pos["bars"] += 1
            pxa, pxb = float(ca.iloc[i]), float(cb.iloc[i])
            la, lb = pos["legs"]
            reason = None
            # per-leg safety stops (pair broken -> close both)
            if (la["side"] == "buy" and pxa <= la["stop"]) or \
               (la["side"] == "sell" and pxa >= la["stop"]):
                reason = "stopA"
            elif (lb["side"] == "buy" and pxb <= lb["stop"]) or \
                 (lb["side"] == "sell" and pxb >= lb["stop"]):
                reason = "stopB"
            else:
                st = med.pairs_state(ca.iloc[:i + 1], cb.iloc[:i + 1], mcfg)
                if st is not None and abs(st["z"]) <= exit_z:
                    reason = "converged"
                elif pos["bars"] >= max_hold:
                    reason = "time"
            if reason is not None:
                close_legs(i, reason)
                cooldown = 2

        if cooldown > 0:
            cooldown -= 1
        if pos is None and cooldown <= 0:
            st = med.pairs_state(ca.iloc[:i + 1], cb.iloc[:i + 1], mcfg)
            if st is None or not st["tradable"] or abs(st["z"]) < entry_z:
                continue
            # size: split risk, equalize notionals (dollar-neutral)
            risk_amount = equity * risk_pct / 100.0
            legs, notionals = [], []
            ok = True
            defs = (("sell", ca, a), ("buy", cb, b)) if st["z"] >= 0 else \
                   (("buy", ca, a), ("sell", cb, b))
            for side, series, sym in defs:
                px = float(series.iloc[i])
                hist = series.iloc[:i + 1]
                # ATR proxy from closes (mock-safe): mean abs change * 2
                atr_px = float(hist.diff().abs().rolling(14).mean().iloc[-1] or 0)
                dist = max(atr_px * atr_mult, px * min_pct)
                if dist <= 0 or px <= 0:
                    ok = False
                    break
                qty = (risk_amount / 2) / dist
                stop = px - dist if side == "buy" else px + dist
                legs.append({"side": side, "sym": sym, "entry": px,
                             "qty": qty, "stop": stop,
                             "lo": min(px, stop), "hi": max(px, stop)})
                notionals.append(qty * px)
            if not ok:
                continue
            target_notion = min(notionals)
            for leg in legs:
                leg["qty"] = target_notion / leg["entry"]
            pos = {"legs": legs, "bars": 0}

    return {"symbol": f"{a}/{b} (pair)", "trades": len(pnls), "wins": wins,
            "losses": losses, "pnl": round(equity - capital, 2),
            "equity": round(equity, 2)}


# ------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--bars", type=int, default=400)
    args = ap.parse_args()

    cfg = load_config(Path(__file__).parent / "config.yaml")
    mcfg = cfg["medallion"]
    tf = mcfg.get("timeframe", "15Min")
    start_capital = 10_000.0

    if os.getenv("ALPACA_API_KEY"):
        from engine.broker import Broker
        broker = Broker(cfg, STATE)
        print("(using REAL Alpaca bars)")
    else:
        broker = MockBroker(cfg, STATE)
        print("(using synthetic mock data — set ALPACA keys for real bars)")

    if args.symbol:
        units = [("single", args.symbol)]
    else:
        units = [("single", s) for s in mcfg.get("symbols", [])]
        units += [("pair", tuple(p)) for p in mcfg.get("pairs", []) or []]

    per_unit = start_capital / max(len(units), 1)
    results = []
    cache = {}

    def get_bars(sym):
        if sym not in cache:
            df = broker.bars(sym, tf, args.bars)
            if df is None or df.empty:
                raise SystemExit(f"no data for {sym}")
            cache[sym] = df
        return cache[sym]

    for kind, u in units:
        if kind == "single":
            results.append(backtest_single(u, cfg, get_bars(u), per_unit))
        else:
            a, b = u
            results.append(backtest_pair(a, b, cfg, get_bars(a), get_bars(b), per_unit))

    print(f"\n=== MEDALLION BACKTEST ({len(units)} units x ${per_unit:,.0f}) ===")
    total_trades = total_w = total_l = 0
    total_pnl = 0.0
    for r in results:
        wr = (r["wins"] / r["trades"] * 100) if r["trades"] else 0.0
        print(f"{r['symbol']:<18} trades={r['trades']:<4} win%={wr:5.1f}  "
              f"pnl={r['pnl']:+9.2f}  equity={r['equity']:9.2f}")
        total_trades += r["trades"]
        total_w += r["wins"]
        total_l += r["losses"]
        total_pnl += r["pnl"]
    print("-" * 64)
    twr = (total_w / total_trades * 100) if total_trades else 0.0
    print(f"TOTAL              trades={total_trades:<4} win%={twr:5.1f}  "
          f"pnl={total_pnl:+9.2f}  equity={start_capital + total_pnl:9.2f} "
          f"({total_pnl / start_capital * 100:+.1f}%)")
    print(f"(costs modelled at {COST_BPS:.0f} bps/side; mock data is illustrative — "
          f"validate on real data before paper trading)")


if __name__ == "__main__":
    main()
