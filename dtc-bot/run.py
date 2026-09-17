#!/usr/bin/env python3
"""
DTC Bot — automated trading robot for the "DTC v1.36" Pine Script strategy.

Usage:
    python run.py backtest [--days 180] [--csv data.csv]
    python run.py paper            # 24/7 simulated trading on live data (no keys)
    python run.py live --i-understand-live
    python run.py dash             # print the multi-timeframe dashboard once

Common options:  --config config.yaml  --symbol BTC/USDT:USDT --timeframe 15m
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

import pandas as pd

from dtc_bot import __version__
from dtc_bot.backtest import BacktestConfig, Backtester
from dtc_bot.datafeed import DataFeed, make_exchange
from dtc_bot.engine import Engine
from dtc_bot.mtf import MTFDashboard
from dtc_bot.strategy import StrategyConfig
from dtc_bot.utils import load_config, setup_logging

log = logging.getLogger("dtc_bot.cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dtc-bot",
        description="Automated trading robot for the DTC v1.36 strategy.")
    p.add_argument("--config", default="config.yaml", help="YAML config path")
    p.add_argument("--symbol", help="override config symbol")
    p.add_argument("--timeframe", help="override config timeframe")
    p.add_argument("--version", action="version",
                   version=f"dtc-bot {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="run a historical backtest")
    bt.add_argument("--days", type=int, help="days of history to fetch")
    bt.add_argument("--csv", help="load OHLCV from CSV instead of the exchange "
                                  "(columns: time,open,high,low,close[,volume])")
    bt.add_argument("--out", default="data/backtest",
                    help="output directory for trades/equity CSVs")

    sub.add_parser("paper", help="24/7 paper trading (live data, fake money)")

    lv = sub.add_parser("live", help="24/7 live trading (REAL money)")
    lv.add_argument("--i-understand-live", action="store_true",
                    help="required confirmation that orders are real")

    sub.add_parser("dash", help="print the MTF trend dashboard once")
    return p


def cmd_backtest(args, cfg) -> int:
    scfg = StrategyConfig.from_dict(cfg.get("strategy"))
    bcfg = BacktestConfig.from_dict(cfg.get("backtest"))

    if args.csv:
        df = pd.read_csv(args.csv)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.set_index("time").sort_index()
        log.info("Loaded %d candles from %s", len(df), args.csv)
    else:
        days = args.days or int(cfg.get("backtest", {}).get("days", 90))
        exchange = make_exchange(cfg.get("exchange", "binanceusdm"))
        feed = DataFeed(exchange, cfg["symbol"], cfg["timeframe"])
        log.info("Fetching %d days of %s %s candles from %s ...",
                 days, cfg["symbol"], cfg["timeframe"], cfg.get("exchange"))
        df = feed.fetch_history(days=days)
    if df.empty:
        log.error("No data — nothing to backtest.")
        return 1
    log.info("Backtesting on %d closed candles (%s -> %s)",
             len(df), df.index[0], df.index[-1])

    strat = cfg.get("strategy", {}) or {}
    bt = Backtester(scfg, bcfg,
                    allow_longs=bool(strat.get("allow_longs", True)),
                    allow_shorts=bool(strat.get("allow_shorts", True)))
    result = bt.run(df)
    print(result.summary())

    os.makedirs(args.out, exist_ok=True)
    trades_path = f"{args.out}/trades.csv"
    equity_path = f"{args.out}/equity.csv"
    pd.DataFrame(result.trades).to_csv(trades_path, index=False)
    result.equity_curve.to_csv(equity_path, header=True)
    log.info("Wrote %s and %s", trades_path, equity_path)
    return 0


def cmd_dash(args, cfg) -> int:
    exchange = make_exchange(cfg.get("exchange", "binanceusdm"))
    dash = MTFDashboard(exchange, cfg["symbol"],
                        (cfg.get("mtf", {}) or {}).get("timeframes"))
    print(f"\nDTC v1.36 multi-timeframe dashboard — {cfg['symbol']}\n")
    print(dash.render())
    return 0


def cmd_engine(args, cfg, mode: str) -> int:
    engine = Engine(cfg, mode=mode,
                    i_understand_live=getattr(args, "i_understand_live", False))
    try:
        engine.run()
    except KeyboardInterrupt:
        log.info("Interrupted — saving state and exiting")
        engine._save_state()
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.symbol:
        cfg["symbol"] = args.symbol
    if args.timeframe:
        cfg["timeframe"] = args.timeframe

    setup_logging((cfg.get("files", {}) or {}).get("log_file",
                                                   "logs/dtc_bot.log"))
    setup_logging()   # ensure console handler exists

    if args.command == "backtest":
        return cmd_backtest(args, cfg)
    if args.command == "dash":
        return cmd_dash(args, cfg)
    if args.command == "paper":
        return cmd_engine(args, cfg, "paper")
    if args.command == "live":
        return cmd_engine(args, cfg, "live")
    return 2


if __name__ == "__main__":
    sys.exit(main())
