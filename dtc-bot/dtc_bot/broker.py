"""
Live broker adapter (ccxt).

Targets USDT-margined perpetual swaps on exchanges with unified ccxt
support (binanceusdm, bybit, okx, ...).  Spot markets do not support
shorting/reduce-only exits — use paper mode there.

TEST ON TESTNET FIRST (``live.testnet: true`` in config).  Exchanges differ
in stop-order parameters; cancel/place calls are wrapped and logged.
"""
from __future__ import annotations

import logging
from typing import List

import ccxt  # type: ignore

from .trade import OpenPosition

log = logging.getLogger("dtc_bot.broker")


class LiveBroker:
    def __init__(self, exchange: ccxt.Exchange, symbol: str,
                 testnet: bool = True, quote: str = "USDT"):
        self.exchange = exchange
        self.symbol = symbol
        self.quote = quote
        if testnet:
            try:
                exchange.set_sandbox_mode(True)
                log.info("Sandbox/testnet mode ENABLED")
            except Exception as exc:
                log.warning("Exchange does not support sandbox mode (%s) — "
                            "orders would be REAL", exc)
        self.market = self.exchange.market(symbol)

    # -- account ---------------------------------------------------------
    def equity(self) -> float:
        bal = self.exchange.fetch_balance()
        total = bal.get("total", {})
        if self.quote in total and total[self.quote] is not None:
            return float(total[self.quote])
        return float(bal.get(self.quote, {}).get("total", 0.0) or 0.0)

    def last_price(self) -> float:
        t = self.exchange.fetch_ticker(self.symbol)
        return float(t.get("last") or t.get("close"))

    def position_qty(self) -> float:
        """Signed position quantity in base units (+long / -short)."""
        try:
            pos = self.exchange.fetch_position(self.symbol)
            qty = float(pos.get("contracts") or 0.0)
            side = pos.get("side")
            if side == "short":
                return -abs(qty)
            return abs(qty) if side == "long" else qty
        except Exception:
            pass
        for p in self.exchange.fetch_positions([self.symbol]):
            qty = float(p.get("contracts") or 0.0)
            if qty:
                return qty if p.get("side") != "short" else -qty
        return 0.0

    # -- order helpers -----------------------------------------------------
    def _amount(self, qty: float) -> float:
        return float(self.exchange.amount_to_precision(self.symbol, abs(qty)))

    def _price(self, price: float) -> float:
        return float(self.exchange.price_to_precision(self.symbol, price))

    def market_order(self, side: str, qty: float, reduce_only: bool = False):
        params = {"reduceOnly": True} if reduce_only else {}
        qty = self._amount(qty)
        log.info("MARKET %s %.8g %s (reduceOnly=%s)",
                 side.upper(), qty, self.symbol, reduce_only)
        return self.exchange.create_order(self.symbol, "market", side, qty,
                                          None, params)

    def cancel_all(self) -> None:
        try:
            self.exchange.cancel_all_orders(self.symbol)
        except Exception as exc:
            log.warning("cancel_all_orders failed: %s", exc)

    def close_position_market(self) -> None:
        qty = self.position_qty()
        if qty > 0:
            self.market_order("sell", qty, reduce_only=True)
        elif qty < 0:
            self.market_order("buy", -qty, reduce_only=True)

    # -- protective exits ---------------------------------------------------
    def place_exit_orders(self, pos: OpenPosition) -> List[str]:
        """Place the SL (stop-market) and TP1..TP4 (limit) reduce-only orders."""
        ids: List[str] = []
        exit_side = "sell" if pos.direction == 1 else "buy"

        # stop-loss, full remaining quantity
        sl_qty = self._amount(pos.qty)
        sl_price = self._price(pos.sl)
        placed = False
        for order_type, param_key in (("STOP_MARKET", "stopPrice"),
                                      ("STOP_MARKET", "triggerPrice"),
                                      ("market", "stopLossPrice")):
            try:
                o = self.exchange.create_order(
                    self.symbol, order_type, exit_side, sl_qty, None,
                    {param_key: sl_price, "reduceOnly": True},
                )
                ids.append(o["id"])
                placed = True
                break
            except Exception as exc:
                log.debug("SL order attempt %s/%s failed: %s",
                          order_type, param_key, exc)
        if not placed:
            log.error("Could not place SL order — ENGINE WILL MANAGE THE "
                      "STOP INTERNALLY (less safe!)")

        # take-profits: 25% each
        for k, tp in enumerate(pos.tps):
            qty = self._amount(pos.initial_qty * 0.25)
            if qty <= 0:
                continue
            try:
                o = self.exchange.create_order(
                    self.symbol, "limit", exit_side, qty,
                    self._price(tp), {"reduceOnly": True},
                )
                ids.append(o["id"])
                log.info("TP%d limit %s @ %s (qty %.8g)", k + 1,
                         exit_side, tp, qty)
            except Exception as exc:
                log.warning("TP%d order failed: %s", k + 1, exc)
        return ids
