# AiTrading — Documentation Index

Summary of all documentation files and what they cover.

## Documents

### [README.md](README.md)
**Operations guide.** Quick start, installation, how to run (`--dry-run`, `--once`, continuous), manual scan tool, configuration reference, database tables, safety features, project structure.

### [DESIGN.md](DESIGN.md)
**Architecture and algorithms.** System architecture diagram, module breakdown, data flow, scoring algorithm details (technical, fundamental, momentum, sentiment), macro-economic overlay (regime classification, sector rotation, parameter adjustments), risk management rules, technology stack.

### [EXPLAINED.md](EXPLAINED.md)
**Plain-language walkthrough.** Explains the dry run output step-by-step for non-finance readers. Covers what each filter does and why, how scoring dimensions work with the math shown, what every indicator (RSI, MACD, SMA, ADX, OBV) actually measures, how buy/sell decisions are made, the macro overlay and its effect, and why the asymmetric risk/reward math works.

### [WORKFLOW.md](WORKFLOW.md)
**Detailed execution workflow.** Complete flow from launch through every scheduled job. Covers initialization, pre-market prep, full trading cycles, re-rank cycles, position monitoring, shutdown, single-run modes (`--once`, `--dry-run`, `--dashboard`), timing summary for a full trading day. Includes API providers per step, rate limits and throttling, caching layers, and daily API call estimates by provider.

### [CLAUDE.md](CLAUDE.md)
**AI assistant context.** Python environment, key files, module layout, documentation maintenance rules. Read by Claude Code at the start of every session.

### [config.yaml](config.yaml)
**All tunable parameters.** Trading thresholds, scoring weights, screener filters, schedule timing, macro settings, logging config, database path.

### [dashboard/](dashboard/)
**Web dashboard.** Flask-based local web UI for monitoring the trading system. Launch with `python main.py --dashboard`. Pages: overview, positions, rankings, orders, per-stock analysis, portfolio. Read-only database access, dark theme, interactive charts and tables.
