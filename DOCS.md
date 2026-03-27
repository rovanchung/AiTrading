# AiTrading — Documentation Index

Summary of all documentation files and what they cover.

## Documents

### [README.md](README.md)
**Operations guide.** Quick start, installation, how to run (`--dry-run`, `--once`, continuous), manual scan tool, configuration reference, database tables, safety features, project structure.

### [DESIGN.md](DESIGN.md)
**Architecture and algorithms.** System architecture diagram, module breakdown, data flow, scoring algorithm details (technical, fundamental, momentum, sentiment), macro-economic overlay (regime classification, sector rotation, parameter adjustments), risk management rules, technology stack.

### [EXPLAINED.md](EXPLAINED.md)
**Plain-language walkthrough.** Explains the dry run output step-by-step for non-finance readers. Covers what each filter does and why, how scoring dimensions work with the math shown, what every indicator (RSI, MACD, SMA, ADX, OBV) actually measures, how buy/sell decisions are made, the macro overlay and its effect, and why the asymmetric risk/reward math works.

### [CLAUDE.md](CLAUDE.md)
**AI assistant context.** Python environment, key files, module layout, documentation maintenance rules. Read by Claude Code at the start of every session.

### [config.yaml](config.yaml)
**All tunable parameters.** Trading thresholds, scoring weights, screener filters, schedule timing, macro settings, logging config, database path.
