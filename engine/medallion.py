# ============================================================
#  MEDALLION METHOD — quant-style signals (pure Python)
#  Inspired by the principles in "Most_Profitable_Trading_Strategy_
#  Ever_Explained_Simply.pdf" (Jim Simons / Medallion Fund):
#    1. MEAN REVERSION core: fade stretched prices, exit at the mean
#    2. TREND filter/sleeve: buy dips in uptrends; optional breakout
#    3. PAIRS: long the loser + short the winner, exit on convergence
#    4. 100% systematic: every trade comes from rules, never feelings
#
#  This is a *retail-sized interpretation* of those principles —
#  NOT the actual Medallion code (which has never left Renaissance).
# ============================================================
import numpy as np
import pandas as pd

from engine.strategy import atr  # reuse the ATR helper


# ------------------------------------------------------------
# Indicators
# ------------------------------------------------------------
def rsi(close, period=14):
    """Wilder's RSI. Returns a Series aligned with `close`."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # flat market (no gains AND no losses) -> neutral 50
    return out.fillna(50.0)


def zscore(close, lookback=20):
    """How many standard deviations is price from its mean?
    z = -2.5 means 'very stretched below average' (rubber band)."""
    ma = close.rolling(lookback).mean()
    sd = close.rolling(lookback).std(ddof=0)
    return (close - ma) / sd.replace(0, np.nan), ma, sd


# ------------------------------------------------------------
# Single-symbol signal: mean reversion + trend
# ------------------------------------------------------------
def generate_signal(df, mcfg):
    """Return a signal dict or None. `mcfg` is the 'medallion' section.

    Signal format matches engine expectations:
      {side, type, entry, stop, reason, [target_override]}
    `target_override` lets mean-reversion exit AT THE MEAN instead of
    the default R-multiple target.
    """
    mr_n = mcfg.get("mr_lookback", 20)
    trend_n = mcfg.get("trend_len", 50)
    bo_n = mcfg.get("breakout_len", 20)
    need = max(mr_n, trend_n, bo_n) + 5
    if df is None or len(df) < need:
        return None

    close = df["close"]
    px = float(close.iloc[-1])
    if not np.isfinite(px) or px <= 0:
        return None

    z, ma_mr, _ = zscore(close, mr_n)
    z_last = float(z.iloc[-1]) if z.notna().iloc[-1] else 0.0
    mean = float(ma_mr.iloc[-1]) if ma_mr.notna().iloc[-1] else px
    sma_trend = float(close.rolling(trend_n).mean().iloc[-1])
    r = rsi(close, mcfg.get("rsi_period", 2))
    rsi_last = float(r.iloc[-1])

    a = atr(df, mcfg.get("atr_period", 14))
    atr_val = float(a.iloc[-1]) if a.notna().iloc[-1] else 0.0
    atr_mult = mcfg.get("atr_stop_mult", 2.0)
    min_stop = px * mcfg.get("min_stop_pct", 0.003)
    stop_dist = max(atr_val * atr_mult, min_stop)
    if stop_dist <= 0:
        return None

    entry_z = mcfg.get("entry_z", 2.0)
    use_rsi = mcfg.get("use_rsi_filter", True)
    rsi_os = mcfg.get("rsi_oversold", 15)
    rsi_ob = mcfg.get("rsi_overbought", 85)
    # trigger_mode "either" (default): z-score OR RSI extreme fires.
    # "both": stricter — needs both at once (fewer, pickier trades).
    need_both = str(mcfg.get("trigger_mode", "either")).lower() == "both"
    z_long = z_last <= -entry_z
    z_short = z_last >= entry_z
    rsi_long = (not use_rsi) or (rsi_last <= rsi_os)
    rsi_short = (not use_rsi) or (rsi_last >= rsi_ob)
    if need_both:
        long_trigger = z_long and (use_rsi and rsi_last <= rsi_os)
        short_trigger = z_short and (use_rsi and rsi_last >= rsi_ob)
    else:
        long_trigger = z_long or rsi_long
        short_trigger = z_short or rsi_short
        if not use_rsi:
            long_trigger, short_trigger = z_long, z_short

    # Trend filter: is the long average itself RISING (uptrend) or
    # FALLING (downtrend)? Slope-based, so dips below a rising average
    # still qualify ("buy the dip in an uptrend").
    use_trend_filter = mcfg.get("use_trend_filter", False)
    slope_n = mcfg.get("trend_slope_bars", 5)
    sma_series = close.rolling(trend_n).mean()
    slope = float(sma_series.iloc[-1] - sma_series.iloc[-(slope_n + 1)])
    uptrend = slope > 0
    downtrend = slope < 0

    # ---- 1) MEAN REVERSION: buy stretched dips in uptrends ----
    if (long_trigger and (uptrend or not use_trend_filter)):
        stop = px - stop_dist
        sig = {
            "side": "buy",
            "type": "MR",
            "entry": round(px, 6),
            "stop": round(stop, 6),
            "reason": f"mean-reversion long: z={z_last:.2f} rsi={rsi_last:.0f} uptrend",
        }
        if mean > px:  # exit AT THE MEAN (the snap-back target)
            sig["target_override"] = round(mean, 6)
        return sig

    # ---- 2) MEAN REVERSION: short stretched rallies in downtrends ----
    if (short_trigger and (downtrend or not use_trend_filter)):
        stop = px + stop_dist
        sig = {
            "side": "sell",
            "type": "MR",
            "entry": round(px, 6),
            "stop": round(stop, 6),
            "reason": f"mean-reversion short: z=+{z_last:.2f} rsi={rsi_last:.0f} downtrend",
        }
        if 0 < mean < px:
            sig["target_override"] = round(mean, 6)
        return sig

    # ---- 3) TREND SLEEVE (optional): Donchian breakout ----
    if mcfg.get("use_trend_sleeve", False):
        # prior N-bar extremes EXCLUDING the current bar (no lookahead)
        prior_high = float(df["high"].iloc[-(bo_n + 1):-1].max())
        prior_low = float(df["low"].iloc[-(bo_n + 1):-1].min())
        if px > prior_high and uptrend:
            return {
                "side": "buy", "type": "BREAKOUT",
                "entry": round(px, 6),
                "stop": round(px - stop_dist, 6),
                "reason": f"breakout long: {bo_n}-bar high + uptrend",
            }
        if px < prior_low and downtrend:
            return {
                "side": "sell", "type": "BREAKOUT",
                "entry": round(px, 6),
                "stop": round(px + stop_dist, 6),
                "reason": f"breakout short: {bo_n}-bar low + downtrend",
            }

    return None


# ------------------------------------------------------------
# Pairs signal: the "Coke vs Pepsi" trade
# ------------------------------------------------------------
def pairs_state(closes_a, closes_b, mcfg):
    """Analyse the spread between two correlated symbols.

    Returns dict {z, corr, tradable} or None if not enough data.
      z   = how stretched the A/B ratio is (in std devs)
      corr = rolling correlation (must be >= pair_min_corr to trade)
    """
    look = mcfg.get("pair_lookback", 30)
    n = min(len(closes_a), len(closes_b))
    if n < look + 5:
        return None
    a = closes_a.iloc[-n:].reset_index(drop=True)
    b = closes_b.iloc[-n:].reset_index(drop=True)
    b = b.replace(0, np.nan)
    ratio = a / b
    if ratio.isna().iloc[-look:].any():
        return None
    corr = float(a.iloc[-look:].corr(b.iloc[-look:]))
    ma = float(ratio.rolling(look).mean().iloc[-1])
    sd = float(ratio.rolling(look).std(ddof=0).iloc[-1])
    if not np.isfinite(ma) or not np.isfinite(sd) or sd <= 0:
        return None
    z = (float(ratio.iloc[-1]) - ma) / sd
    if not np.isfinite(z):
        return None
    return {"z": z, "corr": corr,
            "tradable": corr >= mcfg.get("pair_min_corr", 0.7)}
