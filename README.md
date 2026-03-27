# AiTrading

Automated stock trading system that scans the S&P 500, scores stocks across technical, fundamental, momentum, and sentiment dimensions, and trades via Alpaca.

## Quick Start

### 1. Install Dependencies

```bash
~/python_env/torch-env/bin/pip install -r requirements.txt
```

### 2. Configure Alpaca API Keys

Sign up for a free paper trading account at [alpaca.markets](https://alpaca.markets), then edit `.env`:

```
ALPACA_API_KEY=your_key_here
ALPACA_SECRET_KEY=your_secret_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

### 3. Initialize Database

```bash
~/python_env/torch-env/bin/python3 setup_db.py
```

### 4. Run

```bash
# Dry run — scan and analyze only, no trades
~/python_env/torch-env/bin/python3 main.py --dry-run

# Single cycle — run one full scan-analyze-trade cycle
~/python_env/torch-env/bin/python3 main.py --once

# Continuous — start the scheduler (runs during market hours)
~/python_env/torch-env/bin/python3 main.py
```

## Operation Modes

### Dry Run (`--dry-run`)

Scans the S&P 500, filters candidates, scores them, and prints the top 20 ranked stocks. No trades are executed. Use this to verify the system is working and review what it would buy.

```
=== TOP CANDIDATES ===
  NFLX    Composite= 56.2  T= 78.0  F= 55.0  M= 25.0  S= 59.0
  NVDA    Composite= 41.1  T= 28.0  F= 70.0  M= 25.0  S= 50.0
  AMZN    Composite= 40.0  T= 30.0  F= 65.0  M= 25.0  S= 47.0
```

### Single Cycle (`--once`)

Runs one complete pipeline: screen → analyze → portfolio evaluate → execute trades → exit. Useful for testing with real broker integration or running manually.

### Continuous (default)

Starts the APScheduler with these jobs:

| Job | Schedule | What it does |
|-----|----------|-------------|
| Pre-market prep | 9:00 AM ET, Mon–Fri | Refresh S&P 500 universe |
| Full cycle | Hourly, 9:35 AM–3:35 PM ET | Screen → Analyze → Trade |
| Position monitor | Every 30 seconds | Check stops, trailing stops, take-profit |
| Re-score holdings | Every 15 minutes | Detect score decay in held positions |

Stop with `Ctrl+C` for graceful shutdown.

## Manual Scan Tool

The `scripts/manual_scan.py` script is useful for debugging and analysis:

```bash
# Scan the full universe, show candidates that pass all filters
~/python_env/torch-env/bin/python3 scripts/manual_scan.py

# Scan and run full analysis on candidates (slower, calls yfinance per ticker)
~/python_env/torch-env/bin/python3 scripts/manual_scan.py --analyze

# Analyze specific tickers with detailed breakdowns
~/python_env/torch-env/bin/python3 scripts/manual_scan.py --tickers AAPL NVDA NFLX

# Show top 5 only
~/python_env/torch-env/bin/python3 scripts/manual_scan.py --analyze --top 5
```

## Configuration

All parameters are in `config.yaml`. Key settings:

### Trading Parameters

```yaml
trading:
  max_positions: 10        # Max simultaneous positions
  buy_threshold: 65        # Min composite score to buy
  sell_threshold: 40       # Sell when score drops below
  stop_loss_pct: 0.05      # 5% hard stop-loss
  trailing_stop_pct: 0.03  # 3% trailing stop from peak
  take_profit_pct: 0.15    # 15% take-profit
  paper_trading: true      # USE PAPER TRADING FIRST
```

### Scoring Weights

```yaml
scoring:
  technical_weight: 0.35
  fundamental_weight: 0.25
  momentum_weight: 0.25
  sentiment_weight: 0.15
```

### Screener Filters

```yaml
screener:
  min_price: 5.0
  max_price: 500.0
  min_avg_volume: 500000
  min_market_cap: 2000000000
  universe: "sp500"
```

## Files and Data

| Path | Purpose |
|------|---------|
| `config.yaml` | All configurable parameters |
| `.env` | API keys (gitignored) |
| `data/trading.db` | SQLite database (positions, scores, orders) |
| `data/logs/trading.log` | Application logs (rotating, 50MB max) |
| `data/logs/alerts.json` | Trading alerts (opens, closes, stops, errors) |

## Database

SQLite with WAL mode. Tables:

- **universe** — S&P 500 tickers, sectors, market caps
- **scores** — Historical analysis scores per ticker
- **positions** — Open and closed positions with entry/exit details
- **orders** — Order log with Alpaca order IDs and fill prices
- **price_snapshots** — Price history from position monitoring
- **portfolio_snapshots** — Portfolio value over time (for drawdown tracking)

## Safety

- **Paper trading by default** — set `trading.paper_trading: true` in config
- **Cash reserve** — 20% of portfolio always held in cash
- **Drawdown protection** — reduces positions at -10%, liquidates at -15%
- **Trailing stops** — locks in gains, sells if price drops 3% from peak
- **Sector limits** — max 30% in any single sector
- **Order retries** — 3 attempts with 2s delay before marking failed

## Project Structure

```
AiTrading/
├── main.py              # Entry point (--dry-run, --once, or continuous)
├── setup_db.py          # Database initialization
├── config.yaml          # Configuration
├── .env                 # API keys
├── core/                # Config, models, database, logging, exceptions
├── screener/            # Universe fetch, filters, screening pipeline
├── analyzer/            # Technical, fundamental, momentum, sentiment scoring
├── portfolio/           # Risk sizing, allocation rules, buy/sell decisions
├── executor/            # Alpaca client, order management
├── monitor/             # Stop-loss, position monitor, alerts
├── orchestrator/        # Trading pipeline, scheduler
├── scripts/             # Manual scan tool
└── data/                # Database and logs (runtime)
```

See [DESIGN.md](DESIGN.md) for architecture details and scoring algorithm documentation.
