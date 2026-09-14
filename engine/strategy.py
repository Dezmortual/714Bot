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
def zigzag_step(pivots, highs, lows, j, lookback):
    """Process the single newly-eligible candle `j` (its +-lookback window
    is complete) into the running pivot list, in place.

    Feeding j = lookback, lookback+1, ... in order reproduces exactly
    what zigzag() computes (zigzag() is a loop over this step), which
    makes it usable for incremental / expanding-window processing.
    Note: it is NOT valid for *sliding* windows — dropping old pivots
    from the front changes the merge history, so recompute zigzag() on
    the window instead.
    """
    wh = highs[j - lookback: j + lookback + 1]
    wl = lows[j - lookback: j + lookback + 1]
    is_h = highs[j] >= wh.max()
    is_l = lows[j] <= wl.min()
    if not (is_h or is_l):
        return pivots
    # prefer the type that is more clearly an extreme
    if is_h and is_l:
        t, p = ("H", highs[j]) if highs[j] - lows[j - lookback] >= 0 else ("L", lows[j])
    elif is_h:
        t, p = "H", highs[j]
    else:
        t, p = "L", lows[j]

    if pivots and pivots[-1][2] == t:
        # merge: keep the more extreme of the same type
        prev = pivots[-1]
        better = (t == "H" and p >= prev[1]) or (t == "L" and p <= prev[1])
        if better:
            pivots[-1] = (j, p, t)
    else:
        pivots.append((j, p, t))
    return pivots


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
        pivots = zigzag_step(pivots, highs, lows, i, lookback)
    return pivots


def swing_points(df, lookback=2):
    zz = zigzag(df, lookback)
    highs = [(i, p) for i, p, t in zz if t == "H"]
    lows = [(i, p) for i, p, t in zz if t == "L"]
    return highs, lows


# ------------------------------------------------------------
# Structure classification
# ------------------------------------------------------------
def _structure_state(sh, sl):
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


def structure_state(df, lookback=2):
    """Classify recent structure as 'bullish', 'bearish', or 'range'.

    Bullish = last two swing highs rising (HH) AND last two swing lows
              rising (HL).
    Bearish = last two swing highs falling (LH) AND last two swing lows
              falling (LL).
    """
    sh, sl = swing_points(df, lookback)
    return _structure_state(sh, sl)


# ------------------------------------------------------------
# W / M formations (double bottom / double top)
# ------------------------------------------------------------
def _detect_w(sh, sl, tol):
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


def detect_w(df, lookback=2, tol=0.0005):
    """W formation: two swing lows at ~same level (double bottom)."""
    sh, sl = swing_points(df, lookback)
    return _detect_w(sh, sl, tol)


def _detect_m(sh, sl, tol):
    if len(sh) < 2:
        return False, None
    h1, h2 = sh[-2], sh[-1]
    scale = h1[1] if h1[1] else 1.0
    if abs(h1[1] - h2[1]) <= tol * scale:
        between = [l for l in sl if h1[0] < l[0] < h2[0]]
        bottom = min(between, key=lambda x: x[1]) if between else None
        return True, (h1, bottom, h2)
    return False, None


def detect_m(df, lookback=2, tol=0.0005):
    """M formation: two swing highs at ~same level (double top)."""
    sh, sl = swing_points(df, lookback)
    return _detect_m(sh, sl, tol)


# ------------------------------------------------------------
# Break of structure
# ------------------------------------------------------------
def _break_of_structure(sh, sl, close):
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
    return _break_of_structure(sh, sl, close)


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
def _signal_from_pivots(pivots, close, lookback, tol, n_bars):
    """Signal logic on a ready-made pivot list (no re-zigzag). Returns the
    same dict/None as generate_signal."""
    if n_bars < lookback * 2 + 6:
        return None

    sh = [(i, p) for i, p, t in pivots if t == "H"]
    sl = [(i, p) for i, p, t in pivots if t == "L"]
    st = _structure_state(sh, sl)
    w_ok, w_pts = _detect_w(sh, sl, tol)
    m_ok, m_pts = _detect_m(sh, sl, tol)
    bos_dir, bos_level = _break_of_structure(sh, sl, close)

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


def generate_signal(df, cfg):
    """Return a signal dict or None. cfg is the 'strategy' section."""
    lookback = cfg.get("swing_lookback", 2)
    tol = cfg.get("w_formation_tolerance", 0.0005)
    pivots = zigzag(df, lookback)
    close = float(df["close"].iloc[-1])
    return _signal_from_pivots(pivots, close, lookback, tol, len(df))
