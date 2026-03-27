"""APScheduler-based job management for trading schedule."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from core.config import Config
from core.database import Database
from executor.alpaca_client import AlpacaClient
from executor.order_manager import OrderManager
from monitor.position_monitor import PositionMonitor
from monitor.alerts import AlertManager
from orchestrator.pipeline import TradingPipeline

logger = logging.getLogger("aitrading.orchestrator.scheduler")


class TradingScheduler:
    """Manages all scheduled trading jobs."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.scheduler = BlockingScheduler()

        # Initialize components
        self.broker = AlpacaClient(config)
        self.alerts = AlertManager()
        self.order_mgr = OrderManager(config, db, self.broker)
        self.pipeline = TradingPipeline(config, db, self.broker, self.order_mgr, self.alerts)
        self.monitor = PositionMonitor(config, db, self.broker, self.order_mgr, self.alerts)

    def setup_jobs(self):
        """Register all scheduled jobs."""
        sc = self.config.schedule

        # Pre-market prep: 9:00 AM ET, Mon-Fri
        self.scheduler.add_job(
            self.pipeline.pre_market_prep,
            CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone="US/Eastern"),
            id="pre_market",
            name="Pre-market prep",
            misfire_grace_time=300,
        )

        # Full trading cycle: every hour during market hours
        scan_interval = sc.get("scan_interval_minutes", 60)
        self.scheduler.add_job(
            self.pipeline.run_full_cycle,
            CronTrigger(
                day_of_week="mon-fri",
                hour="9-15",
                minute=f"35/{scan_interval}" if scan_interval < 60 else "35",
                timezone="US/Eastern",
            ),
            id="full_cycle",
            name="Full scan-analyze-trade cycle",
            misfire_grace_time=300,
        )

        # Position monitoring: every 30 seconds during market hours
        monitor_interval = sc.get("monitor_interval_seconds", 30)
        self.scheduler.add_job(
            self.monitor.check_positions,
            "interval",
            seconds=monitor_interval,
            id="position_monitor",
            name="Position monitor",
        )

        # Re-score holdings: every 15 minutes
        rescore_interval = sc.get("rescore_interval_minutes", 15)
        self.scheduler.add_job(
            self.pipeline.rescore_holdings,
            "interval",
            minutes=rescore_interval,
            id="rescore",
            name="Re-score holdings",
        )

        logger.info("All jobs scheduled:")
        for job in self.scheduler.get_jobs():
            logger.info(f"  - {job.name} ({job.id}): {job.trigger}")

    def start(self):
        """Start the scheduler (blocks)."""
        logger.info("Starting trading scheduler...")
        self.setup_jobs()

        # Run pre-market prep immediately on startup
        logger.info("Running initial pre-market prep...")
        self.pipeline.pre_market_prep()

        logger.info("Scheduler running. Press Ctrl+C to stop.")
        self.scheduler.start()

    def shutdown(self):
        """Gracefully shut down the scheduler."""
        logger.info("Shutting down scheduler...")
        self.scheduler.shutdown(wait=False)
