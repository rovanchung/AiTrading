#!/usr/bin/env python3
"""AiTrading — Automated Stock Trading System.

Usage:
    python main.py              # Start the trading scheduler
    python main.py --once       # Run one full cycle and exit
    python main.py --dry-run    # Run one cycle without executing trades
    python main.py --no-macro   # Disable macro overlay (combine with any mode)
    python main.py --dashboard  # Launch the web dashboard
"""

import argparse
import logging

from core.config import load_config
from core.database import Database
from core.logging_config import setup_logging, setup_transaction_logger
from orchestrator.scheduler import TradingScheduler
from orchestrator.pipeline import TradingPipeline
from executor.alpaca_client import AlpacaClient
from executor.order_manager import OrderManager
from monitor.alerts import AlertManager


def main():
    parser = argparse.ArgumentParser(description="AiTrading - Automated Stock Trading")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only, no trades")
    parser.add_argument("--dashboard", action="store_true", help="Launch web dashboard")
    parser.add_argument("--port", type=int, default=5000, help="Dashboard port (default: 5000)")
    parser.add_argument("--no-macro", action="store_true", help="Disable macro overlay (use base config values)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    args = parser.parse_args()

    # Dashboard mode — launch Flask web UI (no trading dependencies needed)
    if args.dashboard:
        from dashboard.app import create_app
        import yaml
        with open(args.config) as f:
            cfg = yaml.safe_load(f)
        db_path = cfg.get("database", {}).get("path", "data/trading.db")
        app = create_app(db_path=db_path)
        print(f"Starting AiTrading Dashboard at http://127.0.0.1:{args.port}")
        print(f"Database: {app.config['DB_PATH']}")
        app.run(host="127.0.0.1", port=args.port, debug=False)
        return

    # Initialize
    config = load_config(args.config)
    if args.no_macro:
        config.set("macro.enabled", False)
    logger = setup_logging(config)
    setup_transaction_logger(config)
    logger.info("AiTrading starting up...")

    db = Database(config.db_path)
    db.init_schema()

    if args.once or args.dry_run:
        # Single cycle mode
        broker = AlpacaClient(config)
        alerts = AlertManager()
        order_mgr = OrderManager(config, db, broker)
        pipeline = TradingPipeline(config, db, broker, order_mgr, alerts)

        pipeline.pre_market_prep()
        if args.dry_run:
            logger.info("DRY RUN — analyzing only, no trades will be executed")

            # Show macro assessment
            eff_buy = config.trading.get("buy_threshold", 65)
            if pipeline.macro:
                macro = pipeline.macro.get_macro_assessment()
                print("\n=== MACRO ASSESSMENT ===")
                print(f"  Score:  {macro['macro_score']}/100")
                print(f"  Regime: {macro['regime']}")
                print(f"  Cycle:  {macro['cycle_phase']}")
                ind = macro["indicators"]
                print(f"  VIX:    {ind.get('vix', 0):.1f}")
                print(f"  Yield spread: {ind.get('yield_spread', 0):.2f}%")
                print(f"  Market breadth: {ind.get('market_breadth_pct', 0):.0f}%")
                print(f"  SPY vs 200-SMA: {ind.get('spy_distance_pct', 0):+.1f}%")
                adj = macro["adjustments"]
                base_buy = eff_buy
                eff_buy = base_buy + adj.get("buy_threshold", 0)
                base_pos = config.trading.get("max_positions", 10)
                eff_pos = max(1, base_pos + adj.get("max_positions", 0))
                base_cash = config.trading.get("cash_reserve_pct", 0.20)
                eff_cash = min(0.50, base_cash + adj.get("cash_reserve_add", 0))
                print(f"\n  Adjusted parameters:")
                print(f"    Buy threshold: {base_buy} → {eff_buy}")
                print(f"    Max positions: {base_pos} → {eff_pos}")
                print(f"    Cash reserve:  {base_cash:.0%} → {eff_cash:.0%}")
                if adj.get("sector_limits"):
                    print(f"    Sector preferences ({macro['cycle_phase']}):")
                    by_limit = {}
                    for sector, limit in sorted(adj["sector_limits"].items()):
                        by_limit.setdefault(limit, []).append(sector)
                    for limit in sorted(by_limit.keys(), reverse=True):
                        label = "favored" if limit > 0.30 else ("neutral" if limit >= 0.30 else "disfavored")
                        print(f"      {label} ({limit:.0%} cap): {', '.join(sorted(by_limit[limit]))}")
            else:
                print("\n=== MACRO OVERLAY DISABLED ===")
                print(f"  Using base config values (buy threshold = {eff_buy})")

            # Run scan and analyze
            candidates = pipeline.screener.scan()
            if candidates:
                data = pipeline.screener.get_data_for_tickers(candidates)
                spy_df = pipeline.screener._fetch_single("SPY")
                scored = pipeline.analyzer.analyze_batch(candidates, data, spy_df)
                qualifying = [s for s in scored if s.composite >= eff_buy and s.technical >= 50]
                print(f"\n=== TOP CANDIDATES (buy threshold = {eff_buy}) ===")
                for s in scored[:20]:
                    marker = " ✓" if s in qualifying else ""
                    print(
                        f"  {s.ticker:6s}  Composite={s.composite:5.1f}  "
                        f"T={s.technical:5.1f}  F={s.fundamental:5.1f}  "
                        f"M={s.momentum:5.1f}  S={s.sentiment:5.1f}{marker}"
                    )
                print(f"\n  {len(qualifying)} stocks qualify for purchase")
        else:
            pipeline.run_full_cycle()
    else:
        # Scheduler mode (continuous) — Ctrl+C to stop
        scheduler = TradingScheduler(config, db)
        scheduler.start()

    db.close()
    logger.info("AiTrading shut down.")


if __name__ == "__main__":
    main()
