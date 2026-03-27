# AiTrading

Automated stock trading system. See [DOCS.md](DOCS.md) for a summary of all documentation files.

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
- `analyzer/` — technical, fundamental, momentum, sentiment scoring (0-100 each) + economic macro overlay
- `portfolio/` — risk sizing, allocation rules, buy/sell decision engine
- `executor/` — Alpaca broker integration, order management with retries
- `monitor/` — stop-loss, trailing stop, take-profit, position monitoring
- `orchestrator/` — trading pipeline, APScheduler job management

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
| New documentation file | DOCS.md (add entry) |

Always keep DOCS.md in sync as the index of all documentation.

## gstack

- Use the `/browse` skill from gstack for all web browsing tasks
- Never use `mcp__claude-in-chrome__*` tools
- If gstack skills aren't working, run `cd .claude/skills/gstack && ./setup` to build the binary and register skills
- Available gstack skills: `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/design-consultation`, `/review`, `/ship`, `/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`, `/retro`, `/investigate`, `/document-release`, `/codex`, `/cso`, `/autoplan`, `/careful`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`
