"""Unit tests for trade bookkeeping and the backtester — no network."""
import numpy as np
import pandas as pd
import pytest

from dtc_bot.backtest import BacktestConfig, Backtester
from dtc_bot.strategy import StrategyConfig, make_levels
from dtc_bot.trade import OpenPosition, check_intrabar_exits


def make_pos(direction=1, entry=100.0, qty=4.0):
    levels = make_levels(direction, entry,
                         StrategyConfig(stop_loss_pct=0.25))
    return OpenPosition.from_levels(levels, qty, entry_time="t0",
                                    entry_fee=0.0, risk_cash=qty * entry * 0.0025)


def test_sl_takes_priority_over_tp_same_bar():
    """Bar touches SL and TPs -> conservative SL-first fills everything."""
    pos = make_pos(direction=1)
    fills = check_intrabar_exits(pos, high=101.50, low=99.70,
                                 time="t1", fee_pct=0.0)
    assert len(fills) == 1
    assert fills[0].reason == "SL"
    assert fills[0].price == pytest.approx(99.75)
    assert fills[0].qty == pytest.approx(4.0)
    assert pos.qty == pytest.approx(0.0)


def test_tps_close_quarters():
    """Bar reaching TP1 and TP2 closes two 25% chunks at exact TP prices."""
    pos = make_pos(direction=1)
    fills = check_intrabar_exits(pos, high=100.60, low=100.10,
                                 time="t1", fee_pct=0.0)
    reasons = [f.reason for f in fills]
    assert reasons == ["TP1", "TP2"]
    assert fills[0].price == pytest.approx(100.25)
    assert fills[1].price == pytest.approx(100.50)
    assert all(f.qty == pytest.approx(1.0) for f in fills)
    assert pos.qty == pytest.approx(2.0)
    assert pos.tp_hit == [True, True, False, False]


def test_short_mirror_levels():
    pos = make_pos(direction=-1)
    # SL for a short sits ABOVE entry; high piercing it stops us out
    fills = check_intrabar_exits(pos, high=100.30, low=99.00,
                                 time="t1", fee_pct=0.0)
    assert len(fills) == 1 and fills[0].reason == "SL"
    assert fills[0].price == pytest.approx(100.25)

    pos = make_pos(direction=-1)
    fills = check_intrabar_exits(pos, high=100.10, low=99.60,
                                 time="t1", fee_pct=0.0)
    assert [f.reason for f in fills] == ["TP1"]   # short TP1 = 99.75


def test_backtester_smoke_on_synthetic_market():
    closes = (
        [100.0] * 150
        + [100.0 + i * 1.0 for i in range(1, 80)]
        + [180.0 - i * 1.0 for i in range(1, 80)]
        + [100.0 + i * 0.5 for i in range(1, 80)]
    )
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    closes = np.asarray(closes)
    df = pd.DataFrame(
        {
            "open": np.concatenate([[closes[0]], closes[:-1]]),
            "high": closes + 0.25,
            "low": closes - 0.25,
            "close": closes,
            "volume": np.ones(n),
        },
        index=idx,
    )
    bt = Backtester(
        StrategyConfig(use_atr_filter=False),
        BacktestConfig(starting_equity=10_000, risk_percent=1.0,
                       leverage=1.0, fee_pct=0.05),
    )
    result = bt.run(df)
    s = result.stats
    for key in ("net_pnl_pct", "max_drawdown_pct", "n_trades", "win_rate",
                "profit_factor", "avg_r", "fees_paid", "exit_counts"):
        assert key in s
    assert len(result.equity_curve) == n
    assert s["fees_paid"] >= 0
    assert s["n_trades"] == len(result.trades)
    summary = result.summary()
    assert "BACKTEST RESULTS" in summary
