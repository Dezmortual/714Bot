# ============================================================
#  714 METHOD — risk & position sizing
# ============================================================

# Reference table straight from the PDF (page "Risk Management"):
# account size -> lot size. Used only as an informational reference;
# live sizing is computed from risk_percent so it adapts to equity.
RISK_TABLE = [
    (100, 0.01), (200, 0.02), (300, 0.03), (400, 0.04), (500, 0.05),
    (600, 0.06), (700, 0.07), (800, 0.08), (900, 0.09), (1000, 0.10),
]


def pip_distance(entry, stop, pip_value):
    """Distance in 'pips' between two prices."""
    if pip_value <= 0:
        return 0.0
    return abs(entry - stop) / pip_value


def position_size(equity, entry, stop, risk_cfg):
    """Return the quantity to trade so that losing from entry to stop
    risks exactly risk_percent % of equity (capped by max_lot_percent)."""
    risk_percent = risk_cfg.get("risk_percent", 1.0)
    max_lot_percent = risk_cfg.get("max_lot_percent", 5.0)
    pip_value = risk_cfg.get("pip_value", 0.01)

    risk_amount = equity * (risk_percent / 100.0)
    dist = abs(entry - stop)
    if dist <= 0:
        return 0.0
    qty = risk_amount / dist
    # cap so position value <= max_lot_percent of equity
    max_qty = equity * (max_lot_percent / 100.0) / entry if entry > 0 else 0.0
    qty = min(qty, max_qty)
    return qty


def reference_lot(equity):
    """Closest lot size from the book's table for a given account size."""
    lot = 0.01
    for size, l in RISK_TABLE:
        if equity >= size:
            lot = l
    return lot
