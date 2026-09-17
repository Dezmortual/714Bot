# ============================================================
#  DTC v1.36 — Risk & Multi-Target Profit Management
# ============================================================

def position_size(equity, entry, stop, risk_cfg):
    """
    Calculate position size ensuring max risk is bounded.
    - risk_mode: 'percent' (default) risks risk_percent % of equity if SL hit
    - capped by max_lot_percent to prevent over-allocation
    """
    risk_percent = float(risk_cfg.get("risk_percent", 1.0))
    max_lot_percent = float(risk_cfg.get("max_lot_percent", 10.0))

    risk_amount = equity * (risk_percent / 100.0)
    dist = abs(entry - stop)
    if dist <= 0:
        return 0.0

    qty = risk_amount / dist

    # Cap by max portfolio exposure per trade
    if entry > 0:
        max_qty = (equity * (max_lot_percent / 100.0)) / entry
        qty = min(qty, max_qty)
    return qty


def calculate_tp_stages(entry, stop, multipliers=(1.0, 2.0, 3.0, 4.0), side="buy"):
    """
    Computes multi-stage take profit targets:
    TP1 = 1x R, TP2 = 2x R, TP3 = 3x R, TP4 = 4x R
    """
    dist = abs(entry - stop)
    if side == "buy":
        return [entry + dist * m for m in multipliers]
    else:
        return [entry - dist * m for m in multipliers]
