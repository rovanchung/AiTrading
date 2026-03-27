#!/usr/bin/env python3
"""AiTrading — Automated Stock Trading System.

Usage:
    python main.py              # Start the trading scheduler
    python main.py --once       # Run one full cycle and exit
    python main.py --dry-run    # Run one cycle without executing trades
"""

import argparse
import logging
import signal
import sys

from core.config import load_config
from core.database import Database
from core.logging_config import setup_logging
from orchestrator.scheduler import TradingScheduler
from orchestrator.pipeline import TradingPipeline
from executor.alpaca_client import AlpacaClient
from executor.order_manager import OrderManager
from monitor.alerts import AlertManager


def main():
    parser = argparse.ArgumentParser(description="AiTrading - Automated Stock Trading")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only, no trades")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    args = parser.parse_args()

    # Initialize
    config = load_config(args.config)
    logger = setup_logging(config)
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
            # Run scan and analyze only
            candidates = pipeline.screener.scan()
            if candidates:
                data = pipeline.screener.get_data_for_tickers(candidates)
                spy_df = pipeline.screener._fetch_single("SPY")
                scored = pipeline.analyzer.analyze_batch(candidates, data, spy_df)
                print("\n=== TOP CANDIDATES ===")
                for s in scored[:20]:
                    print(
                        f"  {s.ticker:6s}  Composite={s.composite:5.1f}  "
                        f"T={s.technical:5.1f}  F={s.fundamental:5.1f}  "
                        f"M={s.momentum:5.1f}  S={s.sentiment:5.1f}"
                    )
        else:
            pipeline.run_full_cycle()
    else:
        # Scheduler mode (continuous)
        scheduler = TradingScheduler(config, db)

        def shutdown(signum, frame):
            logger.info("Received shutdown signal, stopping...")
            scheduler.shutdown()
            db.close()
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        scheduler.start()

    db.close()
    logger.info("AiTrading shut down.")


if __name__ == "__main__":
    main()
