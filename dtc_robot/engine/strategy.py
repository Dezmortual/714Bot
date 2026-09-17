# ============================================================
#  DTC v1.36 — EMA Ribbon + MTF Confluence Trading Strategy
#  Based on the Pine Script "DTC - v1.36" indicator
#
#  Key Logic:
#  - 6-period EMA Ribbon: EMA 30, 35, 40, 45, 50, 60
#  - Bullish trend: EMA 30 > 35 > 40 > 45 > 50 > 60
#  - Bearish trend: EMA 30 < 35 < 40 < 45 < 50 < 60
#  - Signal Trigger: Trend flips (bullish/bearish) on bar close
#  - Filters:
#      * State check: no repeated signals in the same direction
#      * ATR filter: ATR(14) > atr_min (optional)
#      * Multi-timeframe trend alignment (15M, 30M, 1H, 4H, 1D EMA 20/50)
#  - Risk Management:
#      * Stop Loss: percentage (default 0.25%) or swing low/high
#      * Multi-Target Profit: TP1 (1x SL), TP2 (2x SL), TP3 (3x SL), TP4 (4x SL)
# ============================================================
import numpy as np
import pandas as pd


def ema(series, period):
    """Calculate Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def atr(df, period=14):
    """Calculate Average True Range (Wilder's style / rolling mean)."""
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period).mean()


def calculate_dtc_emas(df, len1=30, len2=35, len3=40, len4=45, len5=50, len6=60):
    """Calculate the 6 EMAs defined in DTC v1.36."""
    c = df["close"]
    return {
        "ema1": ema(c, len1),
        "ema2": ema(c, len2),
        "ema3": ema(c, len3),
        "ema4": ema(c, len4),
        "ema5": ema(c, len5),
        "ema6": ema(c, len6),
    }


def compute_dtc_signals(df, cfg):
    """
    Computes DTC v1.36 indicators, trends, and signals across all bars.
    Returns DataFrame with trend and signal columns.
    """
    len1 = cfg.get("len1", 30)
    len2 = cfg.get("len2", 35)
    len3 = cfg.get("len3", 40)
    len4 = cfg.get("len4", 45)
    len5 = cfg.get("len5", 50)
    len6 = cfg.get("len6", 60)

    use_atr = cfg.get("use_atr", True)
    atr_period = cfg.get("atr_period", 14)
    atr_min = cfg.get("atr_min", 0.5)

    df = df.copy()
    emas = calculate_dtc_emas(df, len1, len2, len3, len4, len5, len6)
    for k, v in emas.items():
        df[k] = v

    # DTC trend conditions
    df["bullish_trend"] = (
        (df["ema1"] > df["ema2"]) &
        (df["ema2"] > df["ema3"]) &
        (df["ema3"] > df["ema4"]) &
        (df["ema4"] > df["ema5"]) &
        (df["ema5"] > df["ema6"])
    )

    df["bearish_trend"] = (
        (df["ema1"] < df["ema2"]) &
        (df["ema2"] < df["ema3"]) &
        (df["ema3"] < df["ema4"]) &
        (df["ema4"] < df["ema5"]) &
        (df["ema5"] < df["ema6"])
    )

    # ATR filter
    atr_s = atr(df, atr_period)
    df["atr"] = atr_s
    if use_atr:
        df["atr_ok"] = (atr_s > atr_min)
    else:
        df["atr_ok"] = True

    # Signal trigger on bar close transition
    bull_prev = df["bullish_trend"].shift(1).fillna(False)
    bear_prev = df["bearish_trend"].shift(1).fillna(False)

    df["long_raw"] = (~bull_prev) & df["bullish_trend"]
    df["short_raw"] = (~bear_prev) & df["bearish_trend"]

    # State tracking: simulate Pine Script's `var int signal_state = 0`
    signal_state = 0
    signals = []
    n = len(df)
    for i in range(n):
        l_raw = bool(df["long_raw"].iloc[i])
        s_raw = bool(df["short_raw"].iloc[i])
        a_ok = bool(df["atr_ok"].iloc[i])

        l_sig = l_raw and (signal_state != 1) and a_ok
        s_sig = s_raw and (signal_state != -1) and a_ok

        if l_sig:
            signal_state = 1
            signals.append("buy")
        elif s_sig:
            signal_state = -1
            signals.append("sell")
        else:
            signals.append(None)

    df["dtc_signal"] = signals
    df["signal_state"] = signal_state
    return df


def calculate_mtf_trend(bars_dict):
    """
    Pine script MTF dashboard check:
    htf_trend = ta.ema(close, 20) > ta.ema(close, 50)
    bars_dict: map of timeframe name -> DataFrame of bars
    Returns dict: {tf: bool (True for Bullish, False for Bearish)}
    """
    mtf_status = {}
    for tf, bdf in bars_dict.items():
        if bdf is not None and len(bdf) >= 50:
            e20 = ema(bdf["close"], 20).iloc[-1]
            e50 = ema(bdf["close"], 50).iloc[-1]
            mtf_status[tf] = bool(e20 > e50)
        else:
            mtf_status[tf] = None
    return mtf_status


def generate_signal(df, cfg, mtf_bars=None):
    """
    Generate the latest trading signal for the DTC v1.36 strategy.
    
    Returns a dict with:
      side: 'buy' | 'sell'
      type: 'DTC EMA Ribbon Bullish' | 'DTC EMA Ribbon Bearish'
      entry: price
      stop: stop loss level
      stop_pct: SL %
      tp1, tp2, tp3, tp4: multi-target profit levels
      atr: latest atr
      mtf: dictionary of MTF status
    """
    min_warmup = max(cfg.get("len6", 60) + 10, cfg.get("atr_period", 14) + 10)
    if len(df) < min_warmup:
        return None

    df_sig = compute_dtc_signals(df, cfg)
    last_row = df_sig.iloc[-1]
    sig_side = last_row["dtc_signal"]
    if pd.isna(sig_side) or sig_side not in ("buy", "sell"):
        return None

    # Also compute MTF trends if higher timeframe data provided
    mtf_info = {}
    if mtf_bars:
        mtf_info = calculate_mtf_trend(mtf_bars)

    # Optional MTF confluence filter: require at least N HTF trends to align
    require_mtf_alignment = cfg.get("require_mtf_alignment", False)
    if require_mtf_alignment and sig_side and mtf_info:
        bullish_count = sum(1 for v in mtf_info.values() if v is True)
        bearish_count = sum(1 for v in mtf_info.values() if v is False)
        if sig_side == "buy" and bullish_count < cfg.get("min_mtf_alignment", 3):
            return None
        if sig_side == "sell" and bearish_count < cfg.get("min_mtf_alignment", 3):
            return None

    if not sig_side:
        return None

    entry = float(last_row["close"])
    sl_pct = float(cfg.get("stop_loss_pct", 0.25)) / 100.0  # e.g. 0.25% = 0.0025
    tp1_mult = float(cfg.get("tp1_multiplier", 1.0))
    tp2_mult = float(cfg.get("tp2_multiplier", 2.0))
    tp3_mult = float(cfg.get("tp3_multiplier", 3.0))
    tp4_mult = float(cfg.get("tp4_multiplier", 4.0))

    sl_lookback_mode = cfg.get("sl_lookback", "Mid")
    sl_lookback_bars = {"Tiny": 3, "Small": 5, "Mid": 8, "Large": 12}.get(sl_lookback_mode, 8)

    # Pine script:
    # sl = entry * (1 - stopLossVal/100)
    # tp1 = entry * (1 + stopLossVal * tp1Multiplier/100)
    # tp2 = entry * (1 + stopLossVal * tp2Multiplier/100)...
    # Also support swing lookback as alternative or stop-loss distance
    use_percentage_sl = cfg.get("use_percentage_sl", True)
    
    if sig_side == "buy":
        if use_percentage_sl:
            stop = entry * (1.0 - sl_pct)
        else:
            # Swing low stop
            swing_low = float(df["low"].iloc[-sl_lookback_bars:].min())
            stop = min(swing_low, entry * (1.0 - sl_pct))

        dist = abs(entry - stop)
        tp1 = entry + dist * tp1_mult
        tp2 = entry + dist * tp2_mult
        tp3 = entry + dist * tp3_mult
        tp4 = entry + dist * tp4_mult
        pattern_type = "DTC EMA Ribbon Bullish Alignment (30>35>40>45>50>60)"
        reason = f"DTC v1.36 Bullish Ribbon Expansion + ATR {last_row['atr']:.4f}"
    else:
        if use_percentage_sl:
            stop = entry * (1.0 + sl_pct)
        else:
            swing_high = float(df["high"].iloc[-sl_lookback_bars:].max())
            stop = max(swing_high, entry * (1.0 + sl_pct))

        dist = abs(entry - stop)
        tp1 = entry - dist * tp1_mult
        tp2 = entry - dist * tp2_mult
        tp3 = entry - dist * tp3_mult
        tp4 = entry - dist * tp4_mult
        pattern_type = "DTC EMA Ribbon Bearish Alignment (30<35<40<45<50<60)"
        reason = f"DTC v1.36 Bearish Ribbon Expansion + ATR {last_row['atr']:.4f}"

    return {
        "side": sig_side,
        "type": pattern_type,
        "entry": round(entry, 6),
        "stop": round(stop, 6),
        "tp1": round(tp1, 6),
        "tp2": round(tp2, 6),
        "tp3": round(tp3, 6),
        "tp4": round(tp4, 6),
        "atr": float(last_row["atr"]) if pd.notna(last_row["atr"]) else 0.0,
        "reason": reason,
        "mtf": mtf_info,
        "bullish_trend": bool(last_row["bullish_trend"]),
        "bearish_trend": bool(last_row["bearish_trend"]),
        "price": entry,
    }
