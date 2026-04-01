# AiTrading — System Design

## Overview

AiTrading is an automated stock trading system that continuously scans the US equity market for high-growth stocks, scores them across multiple dimensions, executes trades via Alpaca, and actively monitors positions with protective stops.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Orchestrator                        │
│              (APScheduler + Pipeline)                │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ Pre-mkt  │  │ Full     │  │ Position Monitor  │  │
│  │ 9:00 AM  │  │ Cycle    │  │ every 30 seconds  │  │
│  │ refresh  │  │ hourly   │  │                   │  │
│  └──────────┘  └────┬─────┘  └────────┬──────────┘  │
│                     │                 │              │
└─────────────────────┼─────────────────┼──────────────┘
                      │                 │
        ┌─────────────▼──────────┐      │
        │     SCAN → ANALYZE     │      │
        │     → DECIDE → TRADE   │      │
        │                        │      │
        │  1. Screener           │      │
        │     503 → ~100 tickers │      │
        │                        │      │
        │  2. Analyzer           │      │
        │     Score 0-100 each   │      │
        │                        │      │
        │  3. Portfolio Manager  │      │
        │     Buy/sell signals   │      │
        │                        │      │
        │  4. Executor (Alpaca)  │      │
        │     Submit orders      │      │
        └────────────┬───────────┘      │
                     │                  │
                     ▼                  ▼
              ┌─────────────────────────────┐
              │        SQLite Database       │
              │  positions · scores · orders │
              │  universe · price_snapshots  │
              └─────────────────────────────┘
```

## Modules

### core/
Foundation layer shared by all modules.

- **config.py** — Loads `config.yaml` + `.env`. Provides `Config` object with dot-notation access (`config.get("trading.max_positions")`).
- **models.py** — Dataclasses: `Stock`, `ScoreResult`, `Position`, `Order`, `Signal`.
- **database.py** — SQLite with WAL mode. Tables: `universe`, `scan_results`, `scores`, `positions`, `orders`, `price_snapshots`, `portfolio_snapshots`.
- **logging_config.py** — Rotating file + console logging.
- **exceptions.py** — Hierarchy: `AiTradingError` → `ConfigError`, `DataFetchError`, `BrokerError`, `OrderError`, `RiskLimitError`, `DatabaseError`.
- **alpaca_data.py** — Alpaca market data provider: OHLCV bars and news in yfinance-compatible format. Does not support index tickers (^VIX, ^TNX) — those use yfinance directly.
- **fmp_data.py** — Financial Modeling Prep provider: fundamental ratios (P/E, PEG, ROE, margins, etc.) in yfinance-compatible format. Free tier: 250 req/day.
- **data_provider.py** — Unified data layer: routes each data type to its primary provider (Alpaca for OHLCV/news, FMP for fundamentals) with yfinance as fallback for all.

### screener/
Reduces the S&P 500 universe (~503 stocks) to actionable candidates (~50-100).

- **universe.py** — Fetches S&P 500 list from Wikipedia, caches in DB, refreshes weekly.
- **filters.py** — Sequential filter chain:
  1. Price: $5–$500
  2. Volume: 20-day avg > 500K shares
  3. Moving Average: price above 50-day SMA (uptrend)
  4. Relative Strength: outperforming SPY over 1 month
- **screener.py** — Orchestrates batch data download (Alpaca primary, yfinance fallback) + filter pipeline.

### analyzer/
Scores each candidate 0–100 across four dimensions, plus a macro-economic overlay.

| Dimension | Weight | Module | Key Indicators |
|-----------|--------|--------|----------------|
| Technical | 35% | `technical.py` | SMA crossovers (20/50/200), ADX trend strength, RSI momentum, MACD histogram, OBV volume trend, Bollinger width, ATR volatility |
| Fundamental | 25% | `fundamental.py` | P/E, PEG, P/B (valuation); ROE, profit margin, revenue growth (profitability); current ratio, debt/equity, FCF (health) |
| Momentum | 25% | `momentum.py` | 1-month and 3-month returns, momentum acceleration, consecutive up days, relative strength vs SPY |
| Sentiment | 15% | `sentiment.py` | News headline keyword scoring (positive/negative word matching, baseline 50) |

- **scoring.py** — Weighted composite aggregation.
- **analyzer.py** — Orchestrator that runs all sub-analyzers, sanitizes numpy types, persists results.
- **economic.py** — Macro-economic analysis (see below).

### portfolio/
Buy/sell decision engine with risk management. Parameters are dynamically adjusted by the macro overlay.

- **risk.py** — ATR-based position sizing. Risk per trade capped at 2% of portfolio. Calculates stop-loss and take-profit prices.
- **allocation.py** — Enforces: max positions, sector limits (macro-adjusted per sector), cash reserve.
- **manager.py** — Decision logic:
  - **Buy**: composite ≥ buy_threshold (macro-adjusted), technical ≥ 50, open slots, sector/cash checks pass. Tickers sold at a loss are blocked for `cooldown_hours` (default 24h). Tickers being sold in the current cycle are also excluded from buy evaluation.
  - **Sell**: composite drops below buy_threshold, or replacement candidate in ideal top-N outranks held position
  - **Drawdown**: reduce 50% at -10%, liquidate all at -15%
  - Accepts macro adjustments via `set_macro_adjustments()` which modify buy threshold, max positions, cash reserve, and per-sector limits each cycle

### executor/
Alpaca broker integration.

- **alpaca_client.py** — Wraps `alpaca-py` SDK. Market orders for sells (immediate), limit orders for buys (price control). Market clock check.
- **order_manager.py** — Retry logic (3 attempts, 2s delay), order tracking, DB persistence.

### monitor/
Real-time position protection.

- **stop_loss.py** — Checks three exit conditions: hard stop-loss (-5%), trailing stop (-5% from peak), take-profit (+15%). Updates high-water mark.
- **position_monitor.py** — Runs every 30s. Fetches live prices from Alpaca, checks stops, executes exits.
- **alerts.py** — Event logging to JSON file. Levels: INFO (opened/closed), WARNING (stop triggered), CRITICAL (order failed, drawdown).

### orchestrator/
Ties everything together.

- **pipeline.py** — `pre_market_prep()`: refresh universe + macro + full scan + score + cache shortlist. `execute_open()`: trade at market open using cached analysis. `run_full_cycle()`: full universe screen → analyze → evaluate → execute. `run_rerank_cycle()`: re-score cached shortlist (~50 tickers + held positions) and rebalance. All trade execution goes through `_atomic_evaluate_and_execute()` under a shared lock.
- **scheduler.py** — APScheduler jobs:
  - Pre-market prep: 9:25 AM ET (full universe scan + cache)
  - Market open execute: 9:29 AM ET (retries until market opens)
  - Full cycle: hourly at :28, 10–3 PM ET (retries until :40)
  - Re-rank shortlist: every 15 min (re-score top ~50 + held)
  - Position monitor: every 30s

### dashboard/
Read-only web UI for monitoring the trading system. Flask app with Jinja2 templates, Tailwind CSS dark theme, Chart.js for charts, DataTables for interactive tables. Uses a separate SQLite connection with `PRAGMA query_only=ON` — safe to run concurrently with the trading system.

- **app.py** — Flask app factory with template filters (currency, pct, score_color, timeago)
- **db.py** — Read-only DB helper (query, query_one) with request-scoped connections
- **routes/** — Page blueprints: dashboard home, positions, rankings, orders, analysis, portfolio
- **api/data.py** — JSON endpoints for Chart.js (portfolio history, drawdown, sector allocation, score radar, price history) and DataTables (positions, rankings, orders)
- **templates/** — Base layout with sidebar nav + 6 page templates
- **static/** — Custom CSS (dark DataTables theme) and JS (chart helpers, auto-refresh)

## Economic/Macro Analysis (Portfolio Overlay)

The macro module (`analyzer/economic.py`) operates as a **portfolio-level overlay** — it does not score individual stocks but instead adjusts how aggressively the system trades based on broad economic conditions. Set `macro.enabled: false` in `config.yaml` to disable the overlay entirely (base config values are used unchanged).

### How It Works

1. **Fetch 5 macro indicators** (cached for 4 hours):
   - VIX (fear gauge)
   - Yield curve spread (10Y - short-term treasury)
   - Market breadth (% of sector ETFs above 200-day SMA)
   - SPY trend (price vs 200-day SMA)
   - Interest rate trend (10Y treasury direction over 3 months)

2. **Score each indicator 0–100**, compute weighted macro score:
   ```
   Macro = 0.25×VIX + 0.20×Yield + 0.25×Breadth + 0.20×SPY_trend + 0.10×Rates
   ```

3. **Classify regime**:
   - Risk-on (score ≥ 65): aggressive, fully invested
   - Neutral (40–65): default parameters
   - Risk-off (< 40): selective, high cash

4. **Classify economic cycle**: early_recovery, expansion, late_cycle, recession

5. **Adjust portfolio parameters**:

| Parameter | Risk-on | Neutral | Risk-off |
|-----------|---------|---------|----------|
| Buy threshold | 60 | 65 | 75 |
| Max positions | 10 | 8 | 5 |
| Cash reserve | 15% | 20% | 35% |

6. **Adjust sector limits by cycle phase** (Sam Stovall's sector rotation):

| Cycle Phase | Favored (40% cap) | Neutral (30% cap) | Disfavored (15% cap) |
|-------------|-------------------|--------------------|-----------------------|
| Early recovery | Consumer Disc., Financials, Real Estate, Industrials | Tech, Comms, Materials | Utilities, Staples, Healthcare, Energy |
| Expansion | Tech, Comms, Industrials, Materials | Consumer Disc., Financials, Healthcare | Utilities, Staples, Real Estate, Energy |
| Late cycle | Energy, Materials, Industrials, Healthcare | Staples, Utilities, Financials | Tech, Consumer Disc., Comms, Real Estate |
| Recession | Utilities, Staples, Healthcare | Comms, Real Estate, Financials | Consumer Disc., Tech, Industrials, Materials, Energy |

## Data Flow

```
Wikipedia ──► Universe (503 tickers)
                 │
 Alpaca   ──► Screener filters ──► ~50-100 candidates
(yf fallback)                          │
                                Analyzer scores ──► Ranked ScoreResults
 Alpaca ──► OHLCV bars                │
 Alpaca ──► News/sentiment     Portfolio Manager ◄── Macro Overlay
Finnhub ──► Fundamentals ──► SQLite   │               (regime, cycle,
(FMP/yf fallback)        (cached)  Buy/Sell Signals    sector prefs)
                                      │
              Alpaca ◄──────── Executor (orders)
                 │
              Alpaca ◄──────── Monitor (stops)
                 │
  VIX, ^TNX ──► Macro Analyzer ──► Regime + Adjustments
  (yfinance)    sector ETFs (Alpaca)
                    │
                 SQLite ◄──────── All state persisted
```

### Data Source Strategy

| Data Type | Primary | Fallback | Notes |
|-----------|---------|----------|-------|
| Stock OHLCV (SPY, AAPL, etc.) | Alpaca | yfinance | 200 req/min free tier |
| Index data (^VIX, ^TNX, ^IRX) | yfinance | — | Alpaca doesn't support indices |
| Sector ETF bars | Alpaca | yfinance | Used for market breadth |
| News headlines | Alpaca | yfinance | For sentiment scoring |
| Fundamentals (P/E, ROE, etc.) | Finnhub | FMP, yfinance | 60 req/min; cached in SQLite, skips API if < 80 days old |

After 10 consecutive Alpaca failures, the system automatically switches to yfinance-only mode until the next successful Alpaca call.

### Fundamental Data Providers

| Provider | Free Tier | Key Ratios | Update Freq | Notes |
|----------|-----------|------------|-------------|-------|
| **Finnhub** (primary) | 60 calls/min, no daily cap | EPS, BVPS, ROE, ROA, margins, debt/equity, current ratio, FCF, earnings/revenue growth | Quarterly | Primary source; stored in SQLite `fundamentals` table |
| **FMP** (fallback) | 250 calls/day | ROE, margins, debt/equity, current ratio, FCF | Quarterly | Fewer fields (no EPS/BVPS), 24h JSON cache |
| **yfinance** (fallback) | Unlimited (throttled) | EPS, BVPS, ROE, margins, growth, debt/equity, current ratio, FCF | Quarterly | No API key; rate-limited ~0.5s between calls |
| **SimFin** | 500 credits/month, 5K stocks | 80+ indicators, full statements | Quarterly | Bulk download via Python library; good for batch |
| **Alpha Vantage** | 25 calls/day | P/E, PEG, P/B, ROE, ROA, margins, EPS | Quarterly | Too limited for primary use |
| **SEC EDGAR** | Unlimited, no key | Raw financials only (no ratios) | Quarterly | Must compute ratios yourself |

Price-sensitive ratios (P/E, P/B, PEG) are **not stored** — they are computed at runtime from stored EPS/book value + current market price. This avoids stale price data in the DB and ensures accurate valuation at scoring time.

Fundamental data is cached in SQLite (`fundamentals` table) and only refreshed when older than `fundamentals.staleness_days` (default 80 days), since underlying data changes quarterly.

## Scoring Algorithm Detail

Each dimension scores 0–100. The composite is a weighted sum:

```
Composite = 0.35×Technical + 0.25×Fundamental + 0.25×Momentum + 0.15×Sentiment
```

### Technical (100 points max)
- **Trend (40 pts)**: SMA20 > SMA50 (+15), SMA50 > SMA200 (+15), ADX > 25 (+10)
- **Momentum (30 pts)**: RSI 40–70 (+15), MACD histogram positive & rising (+15)
- **Volume (20 pts)**: Volume > 20-day avg (+10), OBV rising over 5 days (+10)
- **Volatility (10 pts)**: Bollinger width 2–15% (+5), ATR/price 1–5% (+5)

### Fundamental (100 points max)
- **Valuation (35 pts)**: P/E <15 (+15) / <25 (+10) / <35 (+5); PEG <1 (+10) / <2 (+5); P/B <3 (+10)
- **Profitability (35 pts)**: ROE >15% (+15) / >10% (+10); margin >10% (+10); revenue growth >10% (+10)
- **Health (30 pts)**: Current ratio >1.5 (+10); D/E <50 (+10); FCF >0 (+10)

### Momentum (100 points max)
- **Returns (50 pts)**: 1-month return tiers (+8 to +25); 3-month return tiers (+8 to +25)
- **Acceleration (30 pts)**: 1-month > 3-month/3 (+15); ≥6 up days in last 10 (+10-15)
- **Relative strength (20 pts)**: Beating SPY 1-month (+10); beating SPY 3-month (+10)

### Sentiment (100 points max)
- Baseline: 50 (neutral)
- Each positive keyword in news headlines: +3
- Each negative keyword: -3
- Clamped to 0–100

## Risk Management Rules

| Rule | Value | Purpose |
|------|-------|---------|
| Max positions | 10 | Diversification |
| Max per stock | 10% of portfolio | Concentration limit |
| Max per sector | 30% of portfolio | Sector diversification |
| Cash reserve | 20% always held | Buying power buffer |
| Risk per trade | 2% of portfolio | Position sizing via ATR |
| Stop-loss | -5% from entry | Hard floor |
| Trailing stop | -3% from peak | Lock in gains |
| Take-profit | +15% from entry | Profit capture |
| Drawdown reduce | -10% from peak | Cut exposure by 50% |
| Drawdown liquidate | -15% from peak | Exit all, pause 24h |

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12 |
| Market data | Alpaca Data API (OHLCV, news), FMP (fundamentals), yfinance (fallback) |
| Broker | Alpaca (paper trading) |
| Technical indicators | pandas-ta |
| Database | SQLite (WAL mode) |
| Scheduling | APScheduler |
| ML/AI (future) | PyTorch, scikit-learn |
| Dashboard | Flask, Tailwind CSS (CDN), Chart.js, DataTables |
| Config | YAML + dotenv |
