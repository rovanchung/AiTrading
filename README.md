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

## Strategy Versions

The system supports two strategy versions, each with its own parameters. Run them simultaneously — each process writes to its own database:

```bash
./aitrade run --version v3 &
./aitrade run --version v4 &
```

| Version | Profit take | Loss cut | Hold time | Hysteresis |
|---------|-------------|----------|-----------|------------|
| **V3** — Custom  | 20% (also 5% vs prior close) | 2% (also 2% vs prior close) | None | Yes (sell < 65, debounced over 4 cycles) |
| **V4** — No hold | 5% | 3% | None | Yes (sell < 58, min 1 share rebalance) |

## Alpaca Accounts

Credential selection is **decoupled from version naming** so you can run the same version against different Alpaca accounts concurrently.

Define any number of freely-named credential pairs in `.env`:

```dotenv
ALPACA_API_KEY_MAIN=PK...
ALPACA_SECRET_KEY_MAIN=xx...
ALPACA_API_KEY_WIFE=PK...
ALPACA_SECRET_KEY_WIFE=yy...
```

Then pick an account per-process with `--account` (preferred for concurrent runs of the same version):

```bash
./aitrade --version v4 --account MAIN run &   # data/trading_v4_main.db
./aitrade --version v4 --account WIFE run &   # data/trading_v4_wife.db
```

Each `--account` value derives a unique DB path (`data/trading_{version}_{account}.db`), so simultaneous processes never fight over the same SQLite file.

If `--account` is omitted, credentials are resolved in this order:
1. `ALPACA_ACCOUNT_V{N}=<suffix>` in `.env` — per-version mapping.
2. `ALPACA_API_KEY_V{N}` / `ALPACA_SECRET_KEY_V{N}` — legacy naming.
3. Bare `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` — final fallback.

Per-account strategy parameters are configured in the `accounts:` section of `config.yaml` — any trading parameter can be overridden per version.

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
| B. Full trading cycle | Hourly at :00, 10 AM–3 PM ET | `schedule.market_open`, `schedule.market_close` | Full universe screen → analyze → profit check + redistribution → execute orders (retries up to 12 min) |
| C. Rebalance cycle | Every 1 min, 9:30 AM–3:59 PM ET | `schedule.rerank_interval_minutes` | Re-score shortlist (~80 tickers) → profit-based sells → score-proportional redistribution → execute orders |

Full cycles refresh the entire universe hourly. Rebalance cycles use a cached shortlist for faster turnaround. Both use the same two-step logic: (1) sell positions hitting profit/loss thresholds (version-dependent) with 2-hour cooldown, (2) redistribute 50% of portfolio value proportionally by score among qualifying stocks. The trading portion is atomic across all jobs via a shared lock.

See [WORKFLOW.md](WORKFLOW.md) for the detailed step-by-step flow, API providers called per step, rate limits, and daily API call estimates.

## Configuration

All parameters are in `config.yaml`. See `./aitrade info` for a full reference.

Fundamental data is cached in SQLite and only refreshed every ~80 days (configurable via `fundamentals.staleness_days`). After 10 consecutive Alpaca failures, the system switches to yfinance-only mode until the next successful call.

The macro overlay automatically adjusts trading parameters based on economic conditions. Set `macro.enabled: false` in `config.yaml` to disable it (base config values are used as-is). See [DESIGN.md](DESIGN.md) for regime/cycle details.

## Files and Data

| Path | Purpose |
|------|---------|
| `config.yaml` | All configurable parameters (including per-account overrides) |
| `.env` | Alpaca credential pairs (any suffix), `ALPACA_ACCOUNT_V{1-4}` mapping, Finnhub, FMP (gitignored) |
| `data/trading_v{1-4}.db` | Default per-version SQLite databases (when `--account` is not passed) |
| `data/trading_{version}_{account}.db` | Per-(version, account) DBs when `--account` is specified |
| `data/logs/main[_{version}[_{account}]].log` | Application logs — split per version/account when `--version` is passed; falls back to `main.log` otherwise (rotating, 50MB max) |
| `data/logs/transactions[_{version}[_{account}]].log` | Transaction log (buy/sell/exit events) — same naming as main log |
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
