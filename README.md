# AiTrading

Automated stock trading system that scans the S&P 500, scores stocks across technical, fundamental, momentum, and sentiment dimensions, and trades via Alpaca.

## Prerequisites

You need API keys from three providers. The free tier of each is sufficient.

| Provider | What it's used for | Free tier | Sign up |
|----------|-------------------|-----------|---------|
| **Alpaca** | Broker (trading, account), OHLCV price bars, news headlines | 200 req/min, unlimited paper trading | [alpaca.markets](https://alpaca.markets) |
| **Finnhub** | Primary fundamentals (EPS, ROE, margins, growth, debt ratios) | 60 req/min, no daily cap | [finnhub.io](https://finnhub.io) |
| **FMP** *(optional)* | Fundamentals fallback (used only when Finnhub fails) | 250 req/day | [financialmodelingprep.com](https://financialmodelingprep.com) |

**yfinance** is also used as a last-resort fallback for all data types and for index tickers (VIX, treasury yields) that Alpaca doesn't support. It requires no API key.

You'll enter these keys during setup below. See [WORKFLOW.md](WORKFLOW.md) for detailed API usage per cycle and daily call estimates.

## Quick Start

1. Sign up for free accounts at the providers above (FMP is optional)
2. Run `./aitrade` and select **Install** to set up dependencies and API keys
3. Run `./aitrade` and select **Setup database**
4. Run `./aitrade` and select **Dry run** to verify everything works

For detailed information on operation modes, configuration, and safety features, run `./aitrade info`.

## Web Dashboard

Launch the interactive web dashboard to monitor positions, rankings, orders, and portfolio performance:

```bash
./aitrade dashboard              # http://127.0.0.1:5000
./aitrade dashboard --port 8080  # custom port
```

The dashboard is **read-only** and can run safely alongside the trading system. Pages:
- **Dashboard** — KPI cards, portfolio chart, open positions, recent orders
- **Positions** — Open/closed positions with P&L tracking
- **Rankings** — All scored stocks ranked by composite score with radar charts
- **Orders** — Complete order history with status badges
- **Analysis** — Per-stock deep dive (scores, fundamentals, price history)
- **Portfolio** — Value over time, sector allocation, drawdown chart

## Scheduler Jobs

In continuous mode (`./aitrade run`), the system runs these jobs automatically:

| Job | Schedule | Config key | What it does |
|-----|----------|------------|--------------|
| A. Pre-market prep | 9:25 AM ET, Mon–Fri | `schedule.prep_minutes_before_open` | Universe refresh, macro assessment, screen ~500, analyze, cache shortlist |
| B. Full trading cycle | Hourly at :00, 10 AM–3 PM ET | `schedule.market_open`, `schedule.market_close` | Full universe screen → analyze → evaluate sells/buys → execute orders (retries up to 12 min) |
| C. Re-rank shortlist | Every 10 min, 9:30 AM–3:59 PM ET | `schedule.rerank_interval_minutes` | Re-fetch and re-score shortlist (~50 tickers) → evaluate sells/buys → execute orders |
| D. Position monitor | Every 30 sec | `schedule.monitor_interval_seconds` | Check stop-loss, trailing stop, take-profit → execute sells |

The first re-rank fires at 9:29:50 AM, completes analysis pre-open, and defers trade execution to exactly 9:30 AM via a timer. Full cycles refresh the entire universe hourly. Re-ranks use a cached shortlist (~50 tickers) for faster turnaround.

Both full cycles (B) and re-ranks (C) can buy and sell. If a held stock drops in ranking and a better candidate exists, it gets replaced automatically. The trading portion (get positions + execute orders) is atomic across all jobs via a shared lock.

See [WORKFLOW.md](WORKFLOW.md) for the detailed step-by-step flow, API providers called per step, rate limits, and daily API call estimates.

## Configuration

All parameters are in `config.yaml`. See `./aitrade info` for a full reference.

Fundamental data is cached in SQLite and only refreshed every ~80 days (configurable via `fundamentals.staleness_days`). After 10 consecutive Alpaca failures, the system switches to yfinance-only mode until the next successful call.

The macro overlay automatically adjusts trading parameters based on economic conditions. Set `macro.enabled: false` in `config.yaml` to disable it (base config values are used as-is). See [DESIGN.md](DESIGN.md) for regime/cycle details.

## Files and Data

| Path | Purpose |
|------|---------|
| `config.yaml` | All configurable parameters |
| `.env` | API keys (gitignored) |
| `data/trading.db` | SQLite database (positions, scores, fundamentals, orders) |
| `data/logs/main.log` | Application logs (rotating, 50MB max) |
| `data/logs/transactions.log` | Transaction log (buy/sell/exit events) |
| `data/logs/alerts.json` | Trading alerts (opens, closes, stops, errors) |

## Project Structure

```
AiTrading/
├── aitrade               # CLI entry point (Python) — interactive menu or ./aitrade <command>
├── main.py               # Trading engine entry point
├── setup_db.py           # Database initialization
├── config.yaml           # Configuration
├── .env                  # API keys (gitignored)
├── core/                 # Config, models, database, logging, data providers
├── screener/             # Universe fetch, filters, screening pipeline
├── analyzer/             # Technical, fundamental, momentum, sentiment, economic scoring
├── portfolio/            # Risk sizing, allocation rules, buy/sell decisions
├── executor/             # Alpaca client, order management
├── monitor/              # Stop-loss, position monitor, alerts
├── orchestrator/         # Trading pipeline, scheduler
├── dashboard/            # Web dashboard (Flask, Chart.js, DataTables)
├── scripts/              # Manual scan tool
└── data/                 # Database and logs (runtime, gitignored)
```

See [DOCS.md](DOCS.md) for a summary of all documentation files.
