"""Unit tests for the DTC v1.36 strategy port — no network required."""
import numpy as np
import pandas as pd
import pytest

from dtc_bot.strategy import (
    DTCStrategy,
    StrategyConfig,
    enrich,
    generate_signals,
    make_levels,
)

CFG = StrategyConfig(use_atr_filter=False)  # deterministic ATR for most tests


def make_df(closes, bar_range: float = 0.5) -> pd.DataFrame:
    """Build an OHLC frame from a close-price series with a fixed bar range."""
    closes = np.asarray(closes, dtype=float)
    half = bar_range / 2.0
    opens = np.concatenate([[closes[0]], closes[:-1]])
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open": opens,
            "high": closes + half,
            "low": closes - half,
            "close": closes,
            "volume": np.ones(len(closes)),
        },
        index=idx,
    )


# ----------------------------------------------------------------------
# SL/TP math — must match the Pine formulas exactly
# ----------------------------------------------------------------------

def test_levels_math_long():
    lv = make_levels(1, 100.0, StrategyConfig(stop_loss_pct=0.25,
                                              tp_multipliers=[1, 2, 3, 4]))
    assert lv.sl == pytest.approx(99.75)
    assert lv.tps == pytest.approx([100.25, 100.50, 100.75, 101.00])


def test_levels_math_short():
    lv = make_levels(-1, 100.0, StrategyConfig(stop_loss_pct=0.25,
                                               tp_multipliers=[1, 2, 3, 4]))
    assert lv.sl == pytest.approx(100.25)
    assert lv.tps == pytest.approx([99.75, 99.50, 99.25, 99.00])


# ----------------------------------------------------------------------
# Signal behaviour — transitions, alternation, no-repeat
# ----------------------------------------------------------------------

def alternating_market():
    """flat -> strong up -> strong down -> strong up"""
    closes = (
        [100.0] * 120
        + [100.0 + i * 1.0 for i in range(1, 61)]           # up to 160
        + [160.0 - i * 1.0 for i in range(1, 61)]           # down to 100
        + [100.0 + i * 1.0 for i in range(1, 61)]           # up to 160
    )
    return make_df(closes)


def test_signals_alternate_without_repeats():
    df = alternating_market()
    sig = generate_signals(df, CFG)
    fired = [int(s) for s in sig if s != 0]
    assert len(fired) >= 3, f"expected at least 3 signals, got {fired}"
    # first signal should be long (the first ramp), then alternate
    assert fired[0] == 1
    assert fired[1] == -1
    assert fired[2] == 1
    # no two consecutive same-side signals (Pine signal_state rule)
    for a, b in zip(fired, fired[1:]):
        assert a != b


def test_no_repeat_while_trend_holds():
    """A strong persistent uptrend must produce exactly ONE long signal."""
    closes = [100.0] * 100 + [100.0 + i * 0.8 for i in range(1, 80)]
    sig = generate_signals(make_df(closes), CFG)
    assert list(sig).count(1) == 1
    assert list(sig).count(-1) == 0


def test_signal_state_blocks_long_when_already_long():
    df = alternating_market()
    e = enrich(df, CFG)
    # locate the first bullish transition
    trans = None
    for i in range(1, len(e)):
        if e["bullish"].iat[i] and not e["bullish"].iat[i - 1]:
            trans = i
            break
    assert trans is not None

    fresh = DTCStrategy(CFG, signal_state=0)
    sig = fresh.process_bar(e.iloc[: trans + 1], -1)
    assert sig is not None and sig.direction == 1
    assert sig.price == pytest.approx(float(e["close"].iat[trans]))

    blocked = DTCStrategy(CFG, signal_state=1)
    assert blocked.process_bar(e.iloc[: trans + 1], -1) is None


# ----------------------------------------------------------------------
# ATR filter gate
# ----------------------------------------------------------------------

def test_atr_filter_blocks_low_volatility():
    """Identical trend flip in ultra-low volatility must be filtered out."""
    closes = [100.0] * 120 + [100.0 + i * 0.003 for i in range(1, 160)]
    df = make_df(closes, bar_range=0.02)  # ATR ~ 0.023 << atr_min 0.5

    filtered = StrategyConfig(use_atr_filter=True, atr_period=14, atr_min=0.5)
    sig = generate_signals(df, filtered)
    assert (sig == 0).all(), "ATR filter should have blocked every signal"

    unfiltered = StrategyConfig(use_atr_filter=False)
    sig2 = generate_signals(df, unfiltered)
    assert (sig2 == 1).sum() >= 1, "without the filter a long must fire"
