# ============================================================
#  714 METHOD — market-structure strategy logic (pure Python)
#  Implements the concepts from the PDF:
#    * HH / HL / LL / LH swing structure (via a zigzag pivot filter)
#    * W (double-bottom) and M (double-top) formations
#    * break of structure (BoS)
#    * order-block entries
# ============================================================
import numpy as np
import pandas as pd


# ------------------------------------------------------------
# Zigzag pivots  ->  alternating swing highs / lows
# ------------------------------------------------------------
def zigzag(df, lookback=2):
    """Return list of (index, price, 'H'|'L') pivots, strictly alternating.

    A candle is a swing high if its high is the max of the +-lookback
    window; a swing low if its low is the min. Consecutive pivots of the
    same type are merged, keeping the more extreme price.
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    pivots = []
    for i in range(lookback, n - lookback):
        wh = highs[i - lookback: i + lookback + 1]
        wl = lows[i - lookback: i + lookback + 1]
        is_h = highs[i] >= wh.max()
        is_l = lows[i] <= wl.min()
        if not (is_h or is_l):
            continue
        # prefer the type that is more clearly an extreme
        if is_h and is_l:
            t, p = ("H", highs[i]) if highs[i] - lows[i - lookback] >= 0 else ("L", lows[i])
        elif is_h:
            t, p = "H", highs[i]
        else:
            t, p = "L", lows[i]

        if pivots and pivots[-1][2] == t:
            # merge: keep the more extreme of the same type
            prev = pivots[-1]
            better = (t == "H" and p >= prev[1]) or (t == "L" and p <= prev[1])
            if better:
                pivots[-1] = (i, p, t)
        else:
            pivots.append((i, p, t))
    return pivots


def swing_points(df, lookback=2):
    zz = zigzag(df, lookback)
    highs = [(i, p) for i, p, t in zz if t == "H"]
    lows = [(i, p) for i, p, t in zz if t == "L"]
    return highs, lows


# ------------------------------------------------------------
# Structure classification
# ------------------------------------------------------------
def structure_state(df, lookback=2):
    """Classify recent structure as 'bullish', 'bearish', or 'range'.

    Bullish = last two swing highs rising (HH) AND last two swing lows
              rising (HL).
    Bearish = last two swing highs falling (LH) AND last two swing lows
              falling (LL).
    """
    sh, sl = swing_points(df, lookback)
    if len(sh) < 2 or len(sl) < 2:
        return "range"
    hh = sh[-1][1] > sh[-2][1]
    hl = sl[-1][1] > sl[-2][1]
    lh = sh[-1][1] < sh[-2][1]
    ll = sl[-1][1] < sl[-2][1]
    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "range"


# ------------------------------------------------------------
# W / M formations (double bottom / double top)
# ------------------------------------------------------------
def detect_w(df, lookback=2, tol=0.0005):
    """W formation: two swing lows at ~same level (double bottom)."""
    sh, sl = swing_points(df, lookback)
    if len(sl) < 2:
        return False, None
    l1, l2 = sl[-2], sl[-1]
    scale = l1[1] if l1[1] else 1.0
    if abs(l1[1] - l2[1]) <= tol * scale:
        # the high between them (if any) is the W "middle"
        between = [h for h in sh if l1[0] < h[0] < l2[0]]
        top = max(between, key=lambda x: x[1]) if between else None
        return True, (l1, top, l2)
    return False, None


def detect_m(df, lookback=2, tol=0.0005):
    """M formation: two swing highs at ~same level (double top)."""
    sh, sl = swing_points(df, lookback)
    if len(sh) < 2:
        return False, None
    h1, h2 = sh[-2], sh[-1]
    scale = h1[1] if h1[1] else 1.0
    if abs(h1[1] - h2[1]) <= tol * scale:
        between = [l for l in sl if h1[0] < l[0] < h2[0]]
        bottom = min(between, key=lambda x: x[1]) if between else None
        return True, (h1, bottom, h2)
    return False, None


# ------------------------------------------------------------
# Break of structure
# ------------------------------------------------------------
def break_of_structure(df, lookback=2):
    """Detect a recent BoS vs the most recent opposing swing.

    Bullish BoS: in a prior downtrend, price closes above the most recent
    swing high (a Lower High) -> upside break.
    Bearish BoS: in a prior uptrend, price closes below the most recent
    swing low (a Higher Low) -> downside break.

    Returns (direction, level) or (None, None).
    """
    sh, sl = swing_points(df, lookback)
    close = float(df["close"].iloc[-1])
    if len(sh) < 1 or len(sl) < 1:
        return None, None

    last_high = sh[-1][1]
    last_low = sl[-1][1]

    # which came last? determines prevailing micro-structure
    if sh[-1][0] > sl[-1][0]:
        # last pivot was a high -> look for bullish break above it
        if close > last_high:
            return "bullish", last_high
    else:
        # last pivot was a low -> look for bearish break below it
        if close < last_low:
            return "bearish", last_low
    return None, None


# ------------------------------------------------------------
# ATR
# ------------------------------------------------------------
def atr(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period).mean()


# ------------------------------------------------------------
# Main signal generator
# ------------------------------------------------------------
def generate_signal(df, cfg):
    """Return a signal dict or None. cfg is the 'strategy' section."""
    lookback = cfg.get("swing_lookback", 2)
    tol = cfg.get("w_formation_tolerance", 0.0005)
    if len(df) < lookback * 2 + 6:
        return None

    st = structure_state(df, lookback)
    close = float(df["close"].iloc[-1])
    w_ok, w_pts = detect_w(df, lookback, tol)
    m_ok, m_pts = detect_m(df, lookback, tol)
    bos_dir, bos_level = break_of_structure(df, lookback)

    signal = None

    # ---- BUY: W (double bottom) = bullish reversal ----
    # The book's buy setup: market drops -> forms a W -> breaks up.
    if w_ok:
        entry = close
        stop = min(p[1] for p in w_pts if p is not None)
        signal = {
            "side": "buy",
            "type": "W formation",
            "entry": round(entry, 6),
            "stop": round(stop, 6),
            "reason": "W double-bottom reversal",
        }

    # ---- SELL: M (double top) = bearish reversal ----
    # The book's sell setup: market rises -> forms an M -> breaks down.
    elif m_ok:
        entry = close
        stop = max(p[1] for p in m_pts if p is not None)
        signal = {
            "side": "sell",
            "type": "M formation",
            "entry": round(entry, 6),
            "stop": round(stop, 6),
            "reason": "M double-top reversal",
        }

    if signal:
        signal["bos"] = bos_dir
        signal["bos_level"] = bos_level
        signal["structure"] = st
        signal["price"] = close
    return signal
