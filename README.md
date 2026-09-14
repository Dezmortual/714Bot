# 📈 714 Method Trading Bot

An automated trading bot that implements **"The 714 Method"** (Mashaya
Mthethwa / Forxee Group) — a **time-and-price** strategy built on market
structure, `W`/`M` (double top/bottom) formations, breaks of structure and
order blocks.

The bot is built on the **Alpaca** broker (paper **and** live), with a
built-in **simulation mode** so you can see it run with zero API keys, plus a
live **web dashboard**.

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
├── app.py               # Flask dashboard + engine launcher + /backtest
├── backtest.py          # backtest engine with chart (mock data or Alpaca bars)
├── config.yaml          # ALL settings (broker, strategy, risk, sessions)
├── requirements.txt
├── .env.example         # copy to .env and add your Alpaca keys
├── engine/
│   ├── strategy.py      # swing/zigzag, structure, W/M, BoS, signals
│   ├── broker.py        # Alpaca wrapper (paper + live)
│   ├── mock_broker.py   # synthetic data + simulated fills (no keys needed)
│   ├── risk.py          # position sizing + the book's lot-size table
│   └── engine.py        # main loop, session gating, trade management
├── state.py             # thread-safe shared state (engine ↔ dashboard)
├── templates/
│   ├── index.html       # dashboard UI (self-contained, no CDN)
│   └── backtest.html    # backtest UI (form + stats + chart + trade table)
└── data/
    ├── trades.csv       # persisted trade log
    └── charts/          # backtest PNG charts (git-ignored)
```

---

## Quick start

### 1. Install

```bash
cd 714bot
pip install -r requirements.txt
```

### 2. Run (simulation — no keys needed)

```bash
python app.py
```

Open **http://localhost:8000**. The engine starts immediately in
`simulation` mode: it generates a synthetic price series with embedded
`W`/`M` patterns, detects them, and "trades" with simulated fills. Click
**Start / Stop** in the header to control the engine.

### 3. Run live / paper with Alpaca

1. Get keys at <https://app.alpaca.markets> (Paper Trading recommended first).
2. `cp .env.example .env` and paste your `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`.
3. Set `broker.mode` in `config.yaml` to `paper` or `live`.
4. `python app.py`

The dashboard header shows the current mode (PAPER / LIVE / SIMULATION).

### 4. Backtest (with chart)

The backtest replays the **exact live logic** — same 60-bar signal window,
W/M formation entries, ATR stop floor, R:R target, breakeven move, partial
+ locked stop, signal/re-entry cooldowns, crypto buy-only — and reports
equity, win rate, profit factor, max drawdown, Sharpe and expectancy (R),
plus a chart.

**Web** — open **http://localhost:8000/backtest** (link in the dashboard
header): pick a symbol + number of bars, hit *Run backtest*. You get a
stats row, a 3-panel chart (candles with entry/exit markers + initial
SL/TP, equity curve, drawdown) and the full trade table.

**CLI**

```bash
python backtest.py                          # synthetic data, AAPL, 500 bars
python backtest.py --symbol BTC/USD         # crypto (buy-only)
python backtest.py --symbol AAPL --limit 1200 --no-chart
```

CLI prints the stats and saves the chart to `data/backtest_<SYMBOL>.png`.

> Data source: real **Alpaca bars** when `ALPACA_API_KEY`/`ALPACA_SECRET_KEY`
> are set, otherwise the synthetic mock series (labelled "synthetic data"
> on the chart). The synthetic series is only for exercising the pipeline —
> backtest on real bars before drawing any conclusions.

---

## Key config (`config.yaml`)

```yaml
broker:
  mode: paper            # paper | live
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

Set these environment variables on your host:

```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
```

Then set `broker.mode: paper` (test) or `live` (real money) in `config.yaml`.

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
