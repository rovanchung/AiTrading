# AiTrading

Automated stock trading system that scans the S&P 500, scores stocks across technical, fundamental, momentum, and sentiment dimensions, and trades via Alpaca.

## Quick Start

1. Sign up for a free paper trading account at [alpaca.markets](https://alpaca.markets)
2. Run `./aitrade` and select **Install** to set up dependencies and API keys
3. Run `./aitrade` and select **Setup database**
4. Run `./aitrade` and select **Dry run** to verify everything works

For detailed information on operation modes, configuration, and safety features, run `./aitrade info`.

## Web Dashboard

Launch the interactive web dashboard to monitor positions, rankings, orders, and portfolio performance:

```bash
python main.py --dashboard              # http://127.0.0.1:5000
python main.py --dashboard --port 8080  # custom port
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

| Job | Schedule | Duration | Config key | What it does |
|-----|----------|----------|------------|--------------|
| Pre-market prep | 9:25 AM ET, Mon–Fri | ~30s | `schedule.prep_minutes_before_open` | Refresh universe, macro, screen all ~500, score, cache shortlist |
| Full cycle | Hourly at :00, 10 AM–3 PM ET | ~20–30s | `schedule.market_open`, `schedule.market_close` | Full universe screen → Analyze → Buy/Sell (retries up to 12 min) |
| Re-rank shortlist | Every 10 min, 9:29:50 AM–3:59 PM ET | ~5–10s | `schedule.rerank_interval_minutes` | Re-score top ~50 + held stocks, rebalance portfolio |
| Position monitor | Every 30 sec | ~1–2s | `schedule.monitor_interval_seconds` | Check stop-loss, trailing stop, take-profit |

The first re-rank fires at 9:29:50 AM, completes analysis pre-open, and defers trade execution to exactly 9:30 AM via a timer. Subsequent re-ranks run every 10 minutes throughout the trading day. Full cycles refresh the entire universe hourly.

The portfolio always reflects the best available stocks from universal ranking — there is no separate "score decay" logic. If a held stock drops in ranking and a better candidate exists, it gets replaced automatically. The trading portion (get positions + execute orders) is atomic across all jobs via a shared lock.

## Configuration

All parameters are in `config.yaml`. See `./aitrade info` for a full reference.

Market data comes from multiple providers with automatic fallbacks:
- **OHLCV/News**: Alpaca Data API (200 req/min free tier)
- **Fundamentals**: Finnhub (60 req/min, no daily cap) → FMP → yfinance
- **Index tickers** (VIX, treasury yields): yfinance only

Fundamental data is cached in SQLite and only refreshed every ~80 days (configurable via `fundamentals.staleness_days`). Requires `FINNHUB_API_KEY` in `.env` (get one free at [finnhub.io](https://finnhub.io)).

After 10 consecutive Alpaca failures, the system switches to yfinance-only mode until the next successful call.

The macro overlay automatically adjusts trading parameters based on economic conditions. See [DESIGN.md](DESIGN.md) for regime/cycle details.

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
├── aitrade               # CLI entry point — run with no args for interactive menu
├── main.py               # Python entry point
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
