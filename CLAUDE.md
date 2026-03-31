# AiTrading

Automated stock trading system.

## Python Environment

- Use `~/python_env/torch-env/bin/python3` to run scripts
- Use `~/python_env/torch-env/bin/pip` to install packages
- Do NOT create project-local venvs or use `--break-system-packages`

## Key Files

- `config.yaml` — all tunable parameters (thresholds, weights, schedule, filters)
- `.env` — API keys: Alpaca, FMP, Finnhub (gitignored, never commit)
- `data/trading.db` — SQLite database (runtime, gitignored)
- `main.py` — entry point: `--dry-run`, `--once`, `--dashboard`, or continuous scheduler
- `scripts/manual_scan.py` — manual analysis tool for debugging

## Module Layout

- `core/` — config, models, database, logging, exceptions, data providers (shared foundation)
  - `alpaca_data.py` — Alpaca market data (OHLCV bars, news); primary source for price/news
  - `finnhub_data.py` — Finnhub fundamentals (EPS, BVPS, ROE, margins, growth, etc.); primary source for fundamental data
  - `fmp_data.py` — Financial Modeling Prep (fundamentals fallback: ROE, margins, etc.)
  - `data_provider.py` — Unified data layer: routes to Alpaca (OHLCV/news), Finnhub→FMP→yfinance (fundamentals), yfinance (fallback + index tickers)
- `screener/` — S&P 500 universe fetch, filter chain, candidate selection
- `analyzer/` — technical, fundamental, momentum, sentiment scoring (0-100 each) + economic macro overlay
- `portfolio/` — risk sizing, allocation rules, buy/sell decision engine
- `executor/` — Alpaca broker integration, order management with retries
- `monitor/` — stop-loss, trailing stop, take-profit, position monitoring
- `orchestrator/` — trading pipeline, APScheduler job management
- `dashboard/` — Flask web dashboard (read-only DB access, dark theme, Chart.js + DataTables)
  - `app.py` — Flask app factory, template filters, standalone entry point
  - `db.py` — Read-only SQLite helper (PRAGMA query_only=ON)
  - `routes/` — Page routes: main, positions, rankings, orders, analysis, portfolio
  - `api/data.py` — JSON endpoints for charts and tables
  - `templates/` — Jinja2 templates with Tailwind CSS dark theme
  - `static/` — Custom CSS and JS (chart helpers, auto-refresh)

## Documentation

See [DOCS.md](DOCS.md) for all documentation summaries and the maintenance guide. **After every code change, check DOCS.md to determine which files need updates.**
