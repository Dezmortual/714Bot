"""
The 24/7 trading engine — replaces you sitting in front of TradingView.

Every poll it:
  1. fetches the latest CLOSED candles (Pine barstate.isconfirmed equivalent),
  2. runs the DTC v1.36 signal logic on the newest confirmed bar,
  3. closes/reverses any open position on an opposite signal,
  4. sizes and opens the new position (risk % of equity through the SL),
  5. manages SL / TP1..TP4 (paper: internal simulation; live: exchange
     reduce-only orders, reconciled each poll),
  6. persists state to disk so a restart never re-fires a signal,
  7. logs the multi-timeframe dashboard and sends notifications.
"""
from __future__ import annotations

import csv
import logging
import os
import time
from typing import Optional

import pandas as pd

from .datafeed import DataFeed, make_exchange
from .mtf import MTFDashboard
from .notify import Notifier
from .state import load_state, save_state
from .strategy import DTCStrategy, StrategyConfig, enrich, make_levels
from .trade import Fill, OpenPosition, check_intrabar_exits, close_fill, unrealized_pnl
from .utils import fmt_money, fmt_price

log = logging.getLogger("dtc_bot.engine")


class Engine:
    def __init__(self, cfg: dict, mode: str = "paper",
                 i_understand_live: bool = False):
        if mode not in ("paper", "live"):
            raise ValueError("mode must be 'paper' or 'live'")
        self.cfg = cfg
        self.mode = mode

        self.scfg = StrategyConfig.from_dict(cfg.get("strategy"))
        self.symbol = cfg["symbol"]
        self.timeframe = cfg["timeframe"]
        self.poll_seconds = int(cfg.get("poll_seconds", 20))
        self.warmup = max(int(cfg.get("warmup_bars", 400)), self.scfg.warmup_bars())
        strat_cfg = cfg.get("strategy", {}) or {}
        self.allow_longs = bool(strat_cfg.get("allow_longs", True))
        self.allow_shorts = bool(strat_cfg.get("allow_shorts", True))
        risk_cfg = cfg.get("risk", {}) or {}
        self.risk_percent = float(risk_cfg.get("risk_percent", 1.0))
        self.max_position_pct = float(risk_cfg.get("max_position_pct", 100.0))

        mode_cfg = cfg.get(mode, {}) or {}
        self.fee_pct = float(mode_cfg.get("fee_pct", 0.05))

        # exchange + data
        api_key = os.environ.get("API_KEY", "")
        secret = os.environ.get("API_SECRET", "")
        password = os.environ.get("API_PASSWORD", "")
        self.exchange = make_exchange(cfg.get("exchange", "binanceusdm"),
                                      api_key, secret, password)
        self.feed = DataFeed(self.exchange, self.symbol, self.timeframe)

        # live broker
        self.broker = None
        self.exit_order_ids: list = []
        if mode == "live":
            live_cfg = cfg.get("live", {}) or {}
            if not live_cfg.get("enabled", False):
                raise PermissionError(
                    "Refusing to start: live.enabled is false in config.yaml")
            if not i_understand_live:
                raise PermissionError(
                    "Refusing to start: pass --i-understand-live to confirm "
                    "real-money trading")
            if not api_key or not secret:
                raise PermissionError(
                    "Refusing to start: API_KEY / API_SECRET env vars required")
            from .broker import LiveBroker
            self.broker = LiveBroker(
                self.exchange, self.symbol,
                testnet=bool(live_cfg.get("testnet", True)))

        # MTF dashboard
        mcfg = cfg.get("mtf", {}) or {}
        self.mtf = MTFDashboard(self.exchange, self.symbol,
                                mcfg.get("timeframes")) if mcfg else None
        self.mtf_log_each_bar = bool(mcfg.get("log_each_bar", True))
        self.mtf_filter = bool(mcfg.get("use_as_filter", False))
        self.mtf_min_agreeing = int(mcfg.get("min_agreeing", 3))

        # files / notifications
        files = cfg.get("files", {}) or {}
        self.state_file = files.get("state_file", "data/bot_state.json")
        self.trades_csv = files.get("trades_csv", "data/trades.csv")
        self.notifier = Notifier.from_config(cfg.get("notifications"))

        # ---- restore state ------------------------------------------
        st = load_state(self.state_file)
        if st.get("symbol") not in (None, self.symbol) or \
           st.get("timeframe") not in (None, self.timeframe):
            log.warning("State file is for %s %s — starting fresh",
                        st.get("symbol"), st.get("timeframe"))
            st = {}
        self.strategy = DTCStrategy(self.scfg, st.get("signal_state", 0))
        self.last_bar_ts: Optional[str] = st.get("last_bar_ts")
        self.equity = float(st.get("equity",
                                   mode_cfg.get("starting_equity", 10_000.0)))
        self.realized_pnl = float(st.get("realized_pnl", 0.0))
        self.fees_paid = float(st.get("fees_paid", 0.0))
        self.pos: Optional[OpenPosition] = (
            OpenPosition.from_dict(st["position"]) if st.get("position") else None)
        self.closed_trades = int(st.get("closed_trades", 0))
        if self.pos:
            log.info("Restored open %s position: qty %.8g @ %s (SL %s)",
                     self.pos.side, self.pos.qty,
                     fmt_price(self.pos.entry), fmt_price(self.pos.sl))

        self._running = False

    # ------------------------------------------------------------------
    # persistence / logging
    # ------------------------------------------------------------------
    def _save_state(self) -> None:
        save_state(self.state_file, {
            "mode": self.mode,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "signal_state": self.strategy.signal_state,
            "last_bar_ts": self.last_bar_ts,
            "equity": self.equity,
            "realized_pnl": self.realized_pnl,
            "fees_paid": self.fees_paid,
            "closed_trades": self.closed_trades,
            "position": self.pos.to_dict() if self.pos else None,
        })

    def _log_fill(self, f: Fill) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.trades_csv)),
                    exist_ok=True)
        new_file = not os.path.exists(self.trades_csv)
        with open(self.trades_csv, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new_file:
                w.writerow(["time", "event", "side", "price", "qty", "fee",
                            "pnl", "equity", "symbol", "timeframe"])
            w.writerow([str(f.time), f.reason, f.side, f.price, f.qty,
                        f.fee, f.pnl, self.equity, self.symbol, self.timeframe])

    def _apply_fills(self, fills: list) -> None:
        for f in fills:
            self.realized_pnl += f.pnl
            self.fees_paid += f.fee
            self.equity += f.pnl - f.fee
            self._log_fill(f)
            emoji = {"SL": "🛑", "REVERSAL": "🔄"}.get(
                f.reason, "🎯" if f.reason.startswith("TP") else "💰")
            log.info("%s %s: %s %.8g @ %s  pnl=%+.2f  equity=%s",
                     emoji, f.reason, f.side.upper(), f.qty,
                     fmt_price(f.price), f.pnl - f.fee, fmt_money(self.equity))
            self.notifier.send(
                f"{emoji} DTC {self.symbol} {self.timeframe} — {f.reason}\n"
                f"{f.side.upper()} {f.qty:.8g} @ {fmt_price(f.price)}\n"
                f"PnL: {f.pnl - f.fee:+.2f} | Equity: {fmt_money(self.equity)}")

    # ------------------------------------------------------------------
    # sizing
    # ------------------------------------------------------------------
    def _size(self, entry: float) -> tuple:
        stop_dist = entry * self.scfg.stop_loss_pct / 100.0
        risk_cash = self.equity * self.risk_percent / 100.0
        qty = risk_cash / stop_dist if stop_dist > 0 else 0.0
        qty_cap = self.equity * self.max_position_pct / 100.0 / entry
        return min(qty, qty_cap), risk_cash

    # ------------------------------------------------------------------
    # trading actions (paper)
    # ------------------------------------------------------------------
    def _paper_close(self, price: float, time_, reason: str) -> None:
        if not self.pos:
            return
        f = close_fill(self.pos, self.pos.qty, price, time_, reason, self.fee_pct)
        self._apply_fills([f])
        self.pos = None
        self.closed_trades += 1

    def _paper_open(self, direction: int, price: float, time_) -> None:
        qty, risk_cash = self._size(price)
        if qty <= 0:
            log.warning("Sizing produced qty=0 — not enough equity; skipping")
            return
        fee = price * qty * self.fee_pct / 100.0
        f = Fill(time=time_, price=price, qty=qty,
                 side="buy" if direction == 1 else "sell",
                 reason="ENTRY", fee=fee, pnl=0.0)
        self.fees_paid += fee
        self.equity -= fee
        self._log_fill(f)
        self.pos = OpenPosition.from_levels(
            make_levels(direction, price, self.scfg),
            qty, time_, fee, risk_cash)
        log.info("🟢 OPEN %s %.8g @ %s | SL %s | TPs %s | risk %.2f",
                 "LONG" if direction == 1 else "SHORT", qty, fmt_price(price),
                 fmt_price(self.pos.sl),
                 [fmt_price(t) for t in self.pos.tps], risk_cash)
        self.notifier.send(
            f"🟢 DTC {self.symbol} {self.timeframe} — OPEN "
            f"{'LONG' if direction == 1 else 'SHORT'}\n"
            f"Entry: {fmt_price(price)}\nSL: {fmt_price(self.pos.sl)}\n"
            f"TPs: {', '.join(fmt_price(t) for t in self.pos.tps)}")

    # ------------------------------------------------------------------
    # signal handling
    # ------------------------------------------------------------------
    def _on_signal(self, sig, price: float, time_) -> None:
        allowed = (sig.direction == 1 and self.allow_longs) or \
                  (sig.direction == -1 and self.allow_shorts)
        log.info("⚡ DTC %s signal @ %s (state machine -> %d)",
                 "LONG" if sig.direction == 1 else "SHORT",
                 fmt_price(sig.price), sig.direction)

        # optional MTF filter (not part of the Pine signals; default off)
        mtf_rows = None
        if self.mtf and (self.mtf_filter or self.mtf_log_each_bar):
            try:
                mtf_rows = self.mtf.snapshot()
                if self.mtf_log_each_bar:
                    log.info("MTF dashboard:\n%s", self.mtf.render(mtf_rows))
            except Exception as exc:
                log.warning("MTF fetch failed: %s", exc)
        if self.mtf_filter and mtf_rows is not None and allowed:
            if not self.mtf.agrees_with(sig.direction, mtf_rows,
                                        self.mtf_min_agreeing):
                log.info("Signal blocked by MTF filter")
                allowed = False

        if self.mode == "paper":
            if self.pos is not None:
                self._paper_close(price, time_, "REVERSAL")
            if allowed:
                self._paper_open(sig.direction, price, time_)
            else:
                log.info("Direction not allowed — staying flat")
        else:
            self._live_on_signal(sig, allowed)

    def _live_on_signal(self, sig, allowed: bool) -> None:
        assert self.broker is not None
        b = self.broker
        try:
            b.cancel_all()
            b.close_position_market()          # flat before anything new
            self.pos = None
            if not allowed:
                return
            equity = b.equity()
            stop_dist = sig.price * self.scfg.stop_loss_pct / 100.0
            risk_cash = equity * self.risk_percent / 100.0
            qty = min(risk_cash / stop_dist,
                      equity * self.max_position_pct / 100.0 / sig.price)
            if qty <= 0:
                log.warning("Live sizing produced qty=0 — skipping")
                return
            side = "buy" if sig.direction == 1 else "sell"
            order = b.market_order(side, qty)
            fill_price = float(order.get("average")
                               or order.get("price") or sig.price)
            levels = make_levels(sig.direction, fill_price, self.scfg)
            qty_filled = float(order.get("filled") or qty)
            fee = 0.0
            try:
                fee = float((order.get("fee") or {}).get("cost") or 0.0)
            except Exception:
                pass
            self.pos = OpenPosition.from_levels(levels, qty_filled,
                                                sig.bar_time, fee, risk_cash)
            log.info("🟢 LIVE OPEN %s %.8g @ %s",
                     "LONG" if sig.direction == 1 else "SHORT",
                     qty_filled, fmt_price(fill_price))
            self.notifier.send(
                f"🟢 LIVE DTC {self.symbol} {self.timeframe} — "
                f"{'LONG' if sig.direction == 1 else 'SHORT'} "
                f"{qty_filled:.8g} @ {fmt_price(fill_price)}")
            self.exit_order_ids = b.place_exit_orders(self.pos)
        except Exception:
            log.exception("Live signal handling failed")

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        self._running = True
        log.info("DTC bot started — %s %s on %s [%s mode] | warm-up %d bars",
                 self.symbol, self.timeframe, self.cfg.get("exchange"),
                 self.mode.upper(), self.warmup)
        log.info("Levels: SL %.3f%% | TP mults %s | risk %.2f%% per trade",
                 self.scfg.stop_loss_pct, self.scfg.tp_multipliers,
                 self.risk_percent)
        while self._running:
            try:
                self.tick()
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("tick error (will retry)")
            self._save_state()
            time.sleep(self.poll_seconds)
        log.info("Engine stopped.")

    def tick(self) -> None:
        df = self.feed.fetch_closed(self.warmup)
        if df.empty or len(df) < 50:
            log.warning("Not enough candle data yet (%d rows)", len(df))
            return
        e = enrich(df, self.scfg)
        last_ts = str(df.index[-1])
        new_bar = last_ts != self.last_bar_ts

        if new_bar:
            self.last_bar_ts = last_ts
            row = e.iloc[-1]
            price = float(row["close"])

            # manage the closed bar's high/low for an older position
            if self.mode == "paper" and self.pos is not None \
                    and str(self.pos.entry_time) != str(self.last_bar_ts):
                fills = check_intrabar_exits(
                    self.pos, float(row["high"]), float(row["low"]),
                    df.index[-1], self.fee_pct)
                if fills:
                    self._apply_fills(fills)
                if self.pos is not None and self.pos.qty <= 0:
                    self.closed_trades += 1
                    self.pos = None

            # new signal? (bar close == entry price, as in Pine)
            sig = self.strategy.process_bar(e, -1)
            if sig is not None:
                self._on_signal(sig, float(row["close"]), df.index[-1])

            upnl = unrealized_pnl(self.pos, price) if self.pos else 0.0
            log.info("bar %s | close %s | equity %s (%+.2f open) | pos %s",
                     last_ts, fmt_price(price),
                     fmt_money(self.equity + upnl), upnl,
                     f"{self.pos.side} {self.pos.qty:.8g}" if self.pos else "flat")

        # tighter tick-level SL/TP management between bar closes
        if self.mode == "paper" and self.pos is not None:
            try:
                px = self.feed.last_price()
            except Exception:
                px = float(e.iloc[-1]["close"])
            fills = check_intrabar_exits(self.pos, px, px,
                                         pd.Timestamp.utcnow(), self.fee_pct)
            if fills:
                self._apply_fills(fills)
            if self.pos is not None and self.pos.qty <= 0:
                self.closed_trades += 1
                self.pos = None

        # live mode: reconcile with exchange
        if self.mode == "live" and self.broker is not None:
            self._live_reconcile()

    def _live_reconcile(self) -> None:
        """Sync internal state with what the exchange reports."""
        b = self.broker
        try:
            qty = b.position_qty()
        except Exception as exc:
            log.warning("position fetch failed: %s", exc)
            return
        if self.pos is None and qty == 0:
            return
        if self.pos is not None and qty == 0:
            log.info("Exchange position is flat (SL/TP filled) — resyncing")
            self.pos = None
            b.cancel_all()
            return
        if self.pos is not None and abs(qty) not in (0, self.pos.qty):
            # partial TP fill on exchange side
            log.info("Position qty changed %.8g -> %.8g (partial TP?)",
                     self.pos.qty, abs(qty))
            self.pos.qty = abs(qty)
            # re-place protective orders for the new size
            b.cancel_all()
            self.exit_order_ids = b.place_exit_orders(self.pos)
