# 🤖 DTC Bot — Automated Trading Robot for the DTC v1.36 Strategy

A fully automated trading robot that trades the **DTC v1.36** TradingView
(Pine Script v5) indicator for you — 24/7, no chart-watching required.

It watches the market on your chosen exchange, computes the exact same
signals the indicator draws on TradingView (EMA-fan trend flips, ATR
filter, no-repeat signal state), and executes the trades automatically
with the indicator's own money-management levels: stop-loss %, and the
TP1 / TP2 / TP3 / TP4 ladder (25% closed at each target).

```
 TradingView indicator ──► signals you have to act on by hand
 DTC Bot              ──► same signals, executed for you automatically
```

---

## ✨ Features

- **Faithful port of DTC v1.36** — six EMAs (30/35/40/45/50/60), strict
  EMA-fan trend detection on confirmed bars, ATR filter, alternating
  no-repeat signal state, SL = `entry ∓ SL%`, TP1..4 = `entry ± SL%×mult`.
- **Three run modes**
  - `backtest` — replay months of history with realistic fees,
    TP partials, equity curve and full stats.
  - `paper` — 24/7 trading on **live exchange data with fake money**
    (great for proving the bot before risking anything). No API keys needed.
  - `live` — real orders on your exchange via
    [ccxt](https://github.com/ccxt/ccxt) (100+ exchanges), with
    exchange-side stop-loss and take-profit orders.
- **Multi-timeframe dashboard** — the indicator's 15m/30m/1H/4H/1D
  EMA(20)/EMA(50) panel, printed to the log every bar; optionally usable
  as a trade filter.
- **Risk-based sizing** — position size = risk % of equity ÷ stop distance.
- **Crash-safe** — state persisted to disk every cycle; a restart resumes
  the open position and never re-fires the last signal.
- **Notifications** — Telegram and/or Discord messages on entries,
  TPs, stop-losses and reversals (optional).
- **Docker ready** — one command to run it on any VPS.

---

## 🧠 The strategy, Pine → Python

| Pine v5 (DTC v1.36) | This bot |
|---|---|
| `ta.ema(close, 30..60)` × 6 | `close.ewm(span=n, adjust=False)` (`strategy.ema`) |
| `bullish_trend = ema1>...>ema6` | strict `>` chain over all six EMAs |
| `not bullish_trend[1] and bullish_trend` | trend-flip detection on **closed** candles |
| `barstate.isconfirmed` | the still-forming candle is dropped before evaluation |
| `ta.atr(atrPeriod) > atrMin` | Wilder RMA true-range gate (identical formula) |
| `var int signal_state` (no same-side repeats) | `DTCStrategy.signal_state`, persisted in `data/bot_state.json` |
| `entry := close` on signal bar | market/limit fill at the signal bar's close |
| `sl := entry*(1 - stopLoss%/100)` | `levels.sl`, stop-market order / simulated stop |
| `tp1..tp4 := entry*(1 ± stopLoss%×mult/100)` | 4 take-profit levels, **25% of the position each** |
| MTF `EMA20>EMA50` table (15/30/60/240/1D) | `dtc_bot/mtf.py` dashboard (+ optional filter) |
| `alertcondition(...)` pings | Telegram / Discord push notifications |

> The Pine `Stop-Loss Lookback` input (Tiny/Small/Mid/Large) only feeds
> `ta.lowest/highest` values that are never used in the v1.36 SL/TP math —
> the stop is purely `stopLossVal %`. The bot is faithful to the actual math.

---

## 🚀 Quickstart

### 1. Install

```bash
git clone <your-repo-url> dtc-bot      # or unzip the download
cd dtc-bot
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Backtest (no keys, ~30 seconds)

```bash
python run.py backtest --days 180
```

```
================  BACKTEST RESULTS  ================
  Start equity      : 10,000.00
  End equity        : ...
  Net PnL           : ...
  Trades (round)    : ...
  Win rate          : ...
  Exit breakdown    : SL=.., TP3=.., REVERSAL=..
=====================================================
```

Results are written to `data/backtest/trades.csv` and `equity.csv`.

You can also backtest your own data:

```bash
python run.py backtest --csv my_candles.csv   # columns: time,open,high,low,close
```

### 3. Paper trading (live market, fake money, no keys)

```bash
python run.py paper
```

The bot wakes up every `poll_seconds`, prints the MTF dashboard each new
bar, opens/closes simulated positions, and persists everything to
`data/bot_state.json` + `data/trades.csv`. Stop with `Ctrl+C`; restarting
resumes exactly where it left off.

### 4. Print the TradingView-style MTF dashboard once

```bash
python run.py dash
```

```
DTC v1.36 multi-timeframe dashboard — BTC/USDT:USDT

+------+----------+
|  TF  |  Trend   |
+------+----------+
|  15m | Bullish  |
|  30m | Bullish  |
|   1h | Bearish  |
|   4h | Bearish  |
|   1d | Bullish  |
+------+----------+
```

---

## 🔴 Live trading (real money)

> ⚠️ **Read this before going live.**
> 1. Run `backtest`, then run `paper` for **at least a few days**.
> 2. Keep `live.testnet: true` first — most big exchanges have a sandbox.
> 3. Live mode targets **USDT-margined perpetual futures** (long & short,
>    `reduceOnly` exits). On **spot** markets shorting is impossible — set
>    `allow_shorts: false`, or stay on paper mode.
> 4. Create API keys with **trade-only** permissions (no withdrawals!).

```bash
cp .env.example .env       # fill in EXCHANGE / API_KEY / API_SECRET
```

In `config.yaml`:

```yaml
live:
  enabled: true        # kill-switch #1
  testnet: true        # false only when you're sure
```

Then:

```bash
python run.py live --i-understand-live    # confirmation flag #2
```

On each signal the bot cancels stale orders, closes any open position at
market (the strategy's reverse rule), opens the new one, and places a
reduce-only stop-market at the SL plus four reduce-only limit orders at
TP1..TP4, reconciling partial TP fills every poll.

Exchanges differ in stop-order parameters — the adapter tries the common
conventions (binanceusdm/bybit/okx style) and logs every attempt. Verify
behaviour on testnet before real size.

---

## ⚙️ Configuration (`config.yaml`)

All strategy defaults mirror the indicator's default inputs:

| key | default | Pine equivalent |
|---|---|---|
| `strategy.ema_lengths` | `[30,35,40,45,50,60]` | EMA 1..6 |
| `strategy.stop_loss_pct` | `0.25` | Stop Loss % |
| `strategy.tp_multipliers` | `[1,2,3,4]` | TP1..TP4 Multiplier |
| `strategy.use_atr_filter` / `atr_period` / `atr_min` | `true / 14 / 0.5` | ATR Filter section |
| `strategy.atr_min_pct` | `null` | *(bot extra)* ATR gate as % of price — handy because Pine's `0.5` is an absolute price value that means very different things on BTC vs a low-priced alt |
| `mtf.timeframes` | `[15m,30m,1h,4h,1d]` | MTF 15/30/60/240/1D |
| `mtf.use_as_filter` | `false` | *(bot extra)* block trades against higher-TF trend |
| `risk.risk_percent` | `1.0` | % of equity risked per trade |
| `backtest.*` / `paper.*` | see file | simulation settings |

Everything can also be overridden per run: `--symbol`, `--timeframe`,
`--days`, or via env vars (`EXCHANGE`, `BOT_SYMBOL`, `BOT_TIMEFRAME`).

---

## 🔔 Notifications (optional)

- **Telegram**: create a bot with [@BotFather](https://t.me/BotFather),
  put `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env`.
- **Discord**: create a webhook in your channel settings, put
  `DISCORD_WEBHOOK_URL` in `.env`.

You'll get a message on every entry, TP hit, stop-loss and reversal.

---

## 🐳 Run it 24/7 with Docker

```bash
cp .env.example .env
docker compose up -d --build      # paper mode by default
docker compose logs -f
```

Or on a plain VPS: `pip install -r requirements.txt`, then
`tmux new -s dtc 'python run.py paper'` (or a systemd unit).

---

## 📤 Push this project to GitHub

```bash
cd dtc-bot
git init
git add .
git commit -m "DTC Bot — automated trading robot for the DTC v1.36 strategy"
git branch -M main
git remote add origin https://github.com/<you>/<your-new-repo>.git
git push -u origin main
```

`.env`, `data/` and `logs/` are already git-ignored, so your API keys can
never leak into the repo.

---

## 🗂 Project layout

```
dtc-bot/
├── run.py                  # CLI: backtest | paper | live | dash
├── config.yaml             # all settings (mirrors the Pine inputs)
├── dtc_bot/
│   ├── strategy.py         # DTC v1.36 port: EMA fan, ATR, signals, SL/TP math
│   ├── trade.py            # position & fill bookkeeping (TP partials)
│   ├── backtest.py         # historical backtester + stats
│   ├── engine.py           # 24/7 live/paper trading loop
│   ├── broker.py           # live order execution via ccxt
│   ├── datafeed.py         # OHLCV fetching, closed-candle handling
│   ├── mtf.py              # multi-timeframe dashboard
│   ├── state.py            # crash-safe state persistence
│   ├── notify.py           # Telegram / Discord
│   └── utils.py            # config & logging helpers
├── pine/DTC_v1_36.pine     # reference copy of the TradingView indicator
├── tests/                  # pytest unit tests (no network needed)
├── Dockerfile / docker-compose.yml
└── requirements.txt
```

## ⚠️ Fidelity notes (read before comparing with TradingView)

- Entries fill at the **signal bar close** — same price the indicator
  draws (its `entry := close`). Live fills are market orders sent moments
  after the close, so small slippage vs TradingView is normal.
- Intra-bar, when one candle touches both SL and a TP, the backtester
  assumes **SL first** (conservative on purpose).
- `ta.ema` / `ta.atr` match TradingView after a warm-up of a few hundred
  bars; the bot always loads `warmup_bars` (default 400) before deciding.
- The default ATR gate (`atr_min: 0.5`) is an *absolute price* value —
  inherited from the indicator. Tune it per asset, or use `atr_min_pct`.

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## ⚖️ License & disclaimer

MIT License — see [LICENSE](LICENSE).

**Trading involves substantial risk of loss. This software is for
educational purposes; it is not financial advice. Past performance
(backtests included) does not guarantee future results. Never trade with
money you cannot afford to lose.**
