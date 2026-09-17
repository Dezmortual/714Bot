# 📈 714 Method Trading Bot

An automated trading bot that implements **"The 714 Method"** (Mashaya
Mthethwa / Forxee Group) — a **time-and-price** strategy built on market
structure, `W`/`M` (double top/bottom) formations, breaks of structure and
order blocks.

The bot uses Alpaca as an optional market-data source and includes a built-in
**simulation mode** so you can see it run with zero API keys, plus a
mobile-friendly web dashboard and phone alerts. It defaults to **signal-only
mode**: it detects setups and sends alerts, but does not place, modify, or
close orders. You review the chart and confirm any trade in your own MT4/MT5
or iTradeBot setup.

---

## iTradeBot integration boundary

iTradeBot's public documentation describes an Android app that connects to a
user-owned MT4/MT5 account. It does not document a public order-submission API
or webhook for third-party robots. This project therefore does **not** attempt
fragile Android UI automation or store iTradeBot/MetaTrader credentials.

The supported mobile workflow is:

1. This service scans the configured market data and applies the 714 rules.
2. It displays the setup and optionally sends a Telegram/JSON webhook alert.
3. You review spread, news, and risk on your phone.
4. You confirm the trade in your own MT4/MT5/iTradeBot setup.

For unattended execution, use a native MT4/MT5 Expert Advisor on a Windows VPS
instead of trying to drive an Android screen. That is a separate execution
adapter and should be validated on demo first.

---

## What the 714 Method says (and how it's coded)

| Book concept | Implementation |
|---|---|
| HH / HL / LL / LH structure | `strategy.structure_state()` — zigzag pivot filter classifies `bullish` / `bearish` / `range` |
| "M" double-top → sell | `strategy.detect_m()` → **SELL** signal |
| "W" double-bottom → buy | `strategy.detect_w()` → **BUY** signal |
| Break of structure | `strategy.break_of_structure()` |
| "You buy at the Order Block" | entry at the swing low (buy) / high (sell) after the reversal pattern |
| Move SL to breakeven at +20 pips | `engine.manage()` → `breakeven_after_pips` |
| Partial profit at +30, lock +20 | `partial_profit_pips`, `lock_pips` |
| "50 pip setup" | `target_pips` (take-profit) |
| Account → lot-size table | `risk.RISK_TABLE` (reference) + `risk_percent` sizing |
| Session windows (09–11, 14–16, 18–20 SAST) | `engine.in_session()` gating (real mode only) |

> **Note on the market:** the book targets forex / Deriv synthetic indices
> (Boom & Crash, Volatility 75/100). Alpaca only offers **US equities &
> crypto**, so the same *structure-based* logic runs on stocks (e.g. AAPL,
> MSFT, SPY) — the concepts (double tops/bottoms, structure breaks) are
> market-agnostic, which is exactly the book's own claim.

---

## Project layout

```
714bot/
├── app.py               # Flask dashboard + engine launcher
├── backtest.py          # offline backtest (mock data or Alpaca bars)
├── config.yaml          # ALL settings (broker, strategy, risk, sessions)
├── requirements.txt
├── .env.example         # copy to .env and add your Alpaca keys
├── engine/
│   ├── strategy.py      # swing/zigzag, structure, W/M, BoS, signals
│   ├── alerts.py        # Telegram + JSON webhook signal delivery
│   ├── broker.py        # Alpaca market-data/order wrapper
│   ├── mock_broker.py   # synthetic data + simulated fills (no keys needed)
│   ├── risk.py          # position sizing + the book's lot-size table
│   └── engine.py        # main loop, session gating, signal/execution modes
├── state.py             # thread-safe shared state (engine ↔ dashboard)
├── templates/index.html # dashboard UI (self-contained, no CDN)
└── data/trades.csv      # persisted trade log
```

---

## Quick start

### 1. Install

```bash
cd 714bot
pip install -r requirements.txt
```

### 2. Run safely (signal-only simulation — no keys needed)

```bash
python app.py
```

Open **http://localhost:8000**. With no Alpaca keys, the engine uses synthetic
bars with embedded `W`/`M` patterns. It detects setups and shows them in the
dashboard, but the default `execution_enabled: false` prevents all orders.

### 3. Run signal-only with market data and phone alerts

1. Get paper-data credentials at <https://app.alpaca.markets> if you need
   stocks/crypto data.
2. `cp .env.example .env` and add `ALPACA_API_KEY` and
   `ALPACA_SECRET_KEY`.
3. Add either Telegram credentials (`TELEGRAM_BOT_TOKEN` and
   `TELEGRAM_CHAT_ID`) or an `ALERT_WEBHOOK_URL`.
4. Keep `broker.execution_enabled: false` in `config.yaml`.
5. Run `python app.py` and confirm the dashboard says `SIGNAL-ONLY`.

The alert contains the symbol, side, W/M pattern, structure, BoS direction,
reference entry, and pattern stop. It is not an order and must be reviewed
before any manual trade.

### 4. Backtest

```bash
python backtest.py                 # synthetic data
python backtest.py --symbol AAPL   # real bars (needs Alpaca keys)
```

---

## Key config (`config.yaml`)

```yaml
broker:
  mode: paper            # data account mode
  execution_enabled: false  # signal-only safety default
alerts:
  enabled: true          # Telegram/webhook delivery; never places orders
strategy:
  symbols: [AAPL, MSFT, SPY]
  timeframe: 15Min
  use_session_filter: true          # only trade in the session windows
  session_windows: ["09:00-11:00", "14:00-16:00", "18:00-20:00"]
  swing_lookback: 2
  w_formation_tolerance: 0.0005     # % tolerance for double tops/bottoms
risk:
  risk_mode: percent
  risk_percent: 1.0                 # % equity risked per trade
  max_lot_percent: 5.0              # anti-overleverage cap
  breakeven_after_pips: 20
  partial_profit_pips: 30
  partial_fraction: 0.5
  lock_pips: 20
  target_pips: 50
engine:
  poll_seconds: 15
```

---

## Deploying

### Push to GitHub

```bash
git init
git add -A
git commit -m "714 Method trading bot"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

> Secrets are **never** committed — `.env` is git-ignored. Add your Alpaca
> keys only via your host's environment-variable settings (see below), never
> into the repo.

### Where to run it

A trading bot is a **long-running process** — GitHub alone won't run it for
you (GitHub Pages is static, and GitHub Actions has a 6-hour limit). Deploy to
one of these:

| Option | Cost | Notes |
|---|---|---|
| **Render** (render.yaml included) | Free tier sleeps → use Starter (~$7/mo) | Push the repo, connect, add env vars, done |
| **Railway.app** | ~$5/mo usage-based | `Procfile` is ready |
| **Fly.io** | small free allowance | `Procfile` works |
| **VPS** (DigitalOcean/Hetzner, ~$4–6/mo) | cheap & always-on | `pip install -r requirements.txt && python app.py` behind `tmux`/`systemd` |

Set these environment variables on your host for signal-only operation:

```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

`ALERT_WEBHOOK_URL` can be used instead of Telegram. Keep
`broker.execution_enabled: false`; the service is designed to alert you so you
can review and confirm trades in your own MT4/MT5/iTradeBot setup. Do not put
broker or app passwords in this repository.

### CI

`.github/workflows/ci.yml` runs an install + import check + backtest smoke
test on every push/PR, so GitHub verifies the bot still works before you merge.

---

## ⚠️ Disclaimer

This is **educational software**, not financial advice. The 714 Method's
marketing claims (e.g. "99.9% win rate") are **not** verified and should be
treated with extreme skepticism — no strategy guarantees profits, and the
built-in "go all in" advice in the source book is dangerous. The bot defaults
to conservative 1% risk sizing. **Always validate on a paper account and
trade live only with money you can afford to lose.**
