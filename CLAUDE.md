# AiTrading

Automated stock trading system. See [DOCS.md](DOCS.md) for a summary of all documentation files.

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

## Documentation Maintenance

When making code changes, check which docs need updating:

| What changed | Update |
|-------------|--------|
| Scoring logic, weights, indicators | DESIGN.md (scoring section), EXPLAINED.md |
| Trading parameters, thresholds, risk rules | DESIGN.md (risk table), README.md (config section) |
| New module or file | DESIGN.md (module section), README.md (project structure), CLAUDE.md (module layout) |
| Macro/economic logic | DESIGN.md (economic section), EXPLAINED.md (macro section) |
| CLI flags, run modes | README.md (operation modes) |
| New config keys | README.md (configuration section), config.yaml |
| Data source or provider changes | DESIGN.md (data flow, tech stack), README.md (config section), EXPLAINED.md (step 1), WORKFLOW.md (API providers/calls) |
| Schedule, job timing, rate limits | WORKFLOW.md (timing summary, rate limits, daily estimates) |
| New documentation file | DOCS.md (add entry) |

Always keep DOCS.md in sync as the index of all documentation.

**After every code change, update the relevant docs before considering the task complete.** Use the table above to determine which files need updates.
