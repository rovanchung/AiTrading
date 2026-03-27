# AiTrading

Automated stock trading system that scans the S&P 500, scores stocks across technical, fundamental, momentum, and sentiment dimensions, and trades via Alpaca.

## Quick Start

1. Sign up for a free paper trading account at [alpaca.markets](https://alpaca.markets)
2. Run `./aitrade` and select **Install** to set up dependencies and API keys
3. Run `./aitrade` and select **Setup database**
4. Run `./aitrade` and select **Dry run** to verify everything works

For detailed information on operation modes, scheduler jobs, configuration, and safety features, run `./aitrade info`.

## Configuration

All parameters are in `config.yaml`. See `./aitrade info` for a full reference.

The macro overlay automatically adjusts trading parameters based on economic conditions. See [DESIGN.md](DESIGN.md) for regime/cycle details.

## Files and Data

| Path | Purpose |
|------|---------|
| `config.yaml` | All configurable parameters |
| `.env` | API keys (gitignored) |
| `data/trading.db` | SQLite database (positions, scores, orders) |
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
├── core/                 # Config, models, database, logging, exceptions
├── screener/             # Universe fetch, filters, screening pipeline
├── analyzer/             # Technical, fundamental, momentum, sentiment, economic scoring
├── portfolio/            # Risk sizing, allocation rules, buy/sell decisions
├── executor/             # Alpaca client, order management
├── monitor/              # Stop-loss, position monitor, alerts
├── orchestrator/         # Trading pipeline, scheduler
├── scripts/              # Manual scan tool
└── data/                 # Database and logs (runtime, gitignored)
```

See [DOCS.md](DOCS.md) for a summary of all documentation files.
