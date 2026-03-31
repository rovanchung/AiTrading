# AiTrading — Documentation Index

**After every code change, update the relevant docs before considering the task complete.** Each entry below lists what the file covers and when to update it.

### [README.md](README.md)
**Operations guide.** Quick start, installation, how to run (`--dry-run`, `--once`, continuous), manual scan tool, configuration reference, database tables, safety features, project structure.
- Update when: trading parameters/thresholds/risk rules change (config section), new module or file added (project structure), CLI flags or run modes change (operation modes), new config keys added (configuration section), data source or provider changes (config section)

### [DESIGN.md](DESIGN.md)
**Architecture and algorithms.** System architecture diagram, module breakdown, data flow, scoring algorithm details (technical, fundamental, momentum, sentiment), macro-economic overlay (regime classification, sector rotation, parameter adjustments), risk management rules, technology stack.
- Update when: scoring logic/weights/indicators change (scoring section), trading parameters/thresholds/risk rules change (risk table), new module or file added (module section), macro/economic logic changes (economic section), data source or provider changes (data flow, tech stack)

### [EXPLAINED.md](EXPLAINED.md)
**Plain-language walkthrough.** Explains the dry run output step-by-step for non-finance readers. Covers what each filter does and why, how scoring dimensions work with the math shown, what every indicator (RSI, MACD, SMA, ADX, OBV) actually measures, how buy/sell decisions are made, the macro overlay and its effect, and why the asymmetric risk/reward math works.
- Update when: scoring logic/weights/indicators change, macro/economic logic changes (macro section), data source or provider changes (step 1)

### [WORKFLOW.md](WORKFLOW.md)
**Detailed execution workflow.** Complete flow from launch through every scheduled job. Covers initialization, pre-market prep, full trading cycles, re-rank cycles, position monitoring, shutdown, single-run modes (`--once`, `--dry-run`, `--dashboard`), timing summary for a full trading day. Includes API providers per step, rate limits and throttling, caching layers, and daily API call estimates by provider.
- Update when: schedule/job timing/rate limits change (timing summary, rate limits, daily estimates), data source or provider changes (API providers/calls)

### [CLAUDE.md](CLAUDE.md)
**AI assistant context.** Python environment, key files, module layout. Read by Claude Code at the start of every session.
- Update when: new module or file added (module layout)

### [config.yaml](config.yaml)
**All tunable parameters.** Trading thresholds, scoring weights, screener filters, schedule timing, macro settings, logging config, database path.
- Update when: new config keys added

### [dashboard/](dashboard/)
**Web dashboard.** Flask-based local web UI for monitoring the trading system. Launch with `python main.py --dashboard`. Pages: overview, positions, rankings, orders, per-stock analysis, portfolio. Read-only database access, dark theme, interactive charts and tables.

### [DOCS.md](DOCS.md) *(this file)*
**Documentation index.** Add an entry here when creating a new documentation file.
