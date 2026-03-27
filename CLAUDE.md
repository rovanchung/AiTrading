# AiTrading

Automated stock trading system. See [DESIGN.md](DESIGN.md) for architecture, scoring algorithms, and module details. See [README.md](README.md) for setup, operation modes, and configuration.

## Python Environment

- Use `~/python_env/torch-env/bin/python3` to run scripts
- Use `~/python_env/torch-env/bin/pip` to install packages
- Do NOT create project-local venvs or use `--break-system-packages`

## Key Files

- `config.yaml` — all tunable parameters (thresholds, weights, schedule, filters)
- `.env` — Alpaca API keys (gitignored, never commit)
- `data/trading.db` — SQLite database (runtime, gitignored)
- `main.py` — entry point: `--dry-run`, `--once`, or continuous scheduler
- `scripts/manual_scan.py` — manual analysis tool for debugging

## Module Layout

- `core/` — config, models, database, logging, exceptions (shared foundation)
- `screener/` — S&P 500 universe fetch, filter chain, candidate selection
- `analyzer/` — technical, fundamental, momentum, sentiment scoring (0-100 each)
- `portfolio/` — risk sizing, allocation rules, buy/sell decision engine
- `executor/` — Alpaca broker integration, order management with retries
- `monitor/` — stop-loss, trailing stop, take-profit, position monitoring
- `orchestrator/` — trading pipeline, APScheduler job management

## gstack

- Use the `/browse` skill from gstack for all web browsing tasks
- Never use `mcp__claude-in-chrome__*` tools
- If gstack skills aren't working, run `cd .claude/skills/gstack && ./setup` to build the binary and register skills
- Available gstack skills: `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/design-consultation`, `/review`, `/ship`, `/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`, `/retro`, `/investigate`, `/document-release`, `/codex`, `/cso`, `/autoplan`, `/careful`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`
