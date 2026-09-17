# 🤖 DTC v1.36 Automated Trading Robot

An automated trading robot engineered from the **TradingView Pine Script "DTC - v1.36"** strategy. 

It executes trades using the **6-Period Exponential Moving Average (EMA) Ribbon**, checks **multi-timeframe EMA 20/50 confluence**, uses an **ATR volatility filter**, and manages open positions with automated **Stop Loss and 4-tier Take Profit (TP1, TP2, TP3, TP4)** trailing logic.

---

## 📈 DTC v1.36 Strategy Core Architecture

| Script Component | Implementation in Robot |
|---|---|
| **6-EMA Ribbon** | `EMA 30`, `EMA 35`, `EMA 40`, `EMA 45`, `EMA 50`, `EMA 60` |
| **Bullish Trend** | `EMA 30 > 35 > 40 > 45 > 50 > 60` (full fanning alignment) |
| **Bearish Trend** | `EMA 30 < 35 < 40 < 45 < 50 < 60` (full fanning alignment) |
| **Entry Trigger** | Bar-close transition into alignment (`!bullish[1] and bullish` or `!bearish[1] and bearish`) |
| **State Filtering** | `signal_state` lock prevents duplicate signals until state flip |
| **ATR Filter** | `ta.atr(14) > atrMin` volatility threshold check |
| **Stop Loss** | Configurable % (`stopLossVal = 0.25%`) or swing low/high lookback |
| **Multi-Stage TP1** | 1.0x SL distance: moves Stop Loss to **Breakeven** |
| **Multi-Stage TP2** | 2.0x SL distance: trails Stop Loss to **TP1** |
| **Multi-Stage TP3** | 3.0x SL distance: trails Stop Loss to **TP2** |
| **Multi-Stage TP4** | 4.0x SL distance: final target, closes position |
| **Multi-Timeframe** | 15M, 30M, 1H, 4H, Daily EMA 20/50 trend confluence |

---

## 🚀 Quick Start

### 1. Run in Simulation Mode (No Keys Needed)

The robot includes an internal simulation mode that generates live ticking bars and executes simulated market fills:

```bash
python app.py
```

Open **http://localhost:8000** in your browser. The live web dashboard displays:
- Portfolio equity & realized PnL
- Active managed trades with dynamic TP1-TP4 status
- Live signals, execution logs, and MTF confluence

### 2. Live / Paper Trading with Alpaca Broker

1. Get paper trading API keys at [Alpaca Markets](https://alpaca.markets).
2. Copy `.env.example` to `.env` and set:
   ```env
   ALPACA_API_KEY=your_key_here
   ALPACA_SECRET_KEY=your_secret_here
   ```
3. Set `broker.mode: paper` in `config.yaml`.
4. Run:
   ```bash
   python app.py
   ```

### 3. Run Strategy Backtest

```bash
python backtest.py                     # Synthetic data backtest
python backtest.py --symbol BTC/USD    # Real bars (with Alpaca keys)
```

---

## ⚙️ Configuration (`config.yaml`)

```yaml
broker:
  mode: paper            # paper | live
strategy:
  symbols: [BTC/USD, ETH/USD, AAPL, MSFT, NVDA, SPY]
  timeframe: 15Min
  len1: 30
  len2: 35
  len3: 40
  len4: 45
  len5: 50
  len6: 60
  use_atr: true
  atr_period: 14
  atr_min: 0.1
  stop_loss_pct: 0.25
  tp1_multiplier: 1.0
  tp2_multiplier: 2.0
  tp3_multiplier: 3.0
  tp4_multiplier: 4.0
risk:
  risk_percent: 1.0     # 1% equity risked per trade
  max_lot_percent: 10.0 # Max 10% portfolio allocation per asset
  max_open_trades: 4
```

---

## 🛡️ Risk Management

- **Position Sizing:** Position size is dynamically computed so that if price hits the stop-loss, you only risk `risk_percent` (e.g. 1%) of total account equity.
- **Buying Power Guard:** Automatically checks cash & margin balances before submitting orders to avoid rejections.
- **Wash-Trade Protection:** Applies re-entry cooldowns to avoid immediate whipsaw entries.
