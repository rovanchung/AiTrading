"""APScheduler-based job management for trading schedule."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
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
        self.scheduler = BackgroundScheduler()

        # Initialize components
        self.broker = AlpacaClient(config)
        self.alerts = AlertManager()
        self.order_mgr = OrderManager(config, db, self.broker)
        self.pipeline = TradingPipeline(config, db, self.broker, self.order_mgr, self.alerts)
        self.monitor = PositionMonitor(
            config, db, self.broker, self.order_mgr, self.alerts,
            trade_lock=self.pipeline._trade_lock,
        )

    def setup_jobs(self):
        """Register all scheduled jobs."""
        sc = self.config.schedule
        open_hour, open_min = [int(x) for x in sc.get("market_open", "09:30").split(":")]
        close_hour = int(sc.get("market_close", "16:00").split(":")[0])
        prep_minutes = sc.get("prep_minutes_before_open", 5)

        # Pre-market prep at 9:25 AM: universe + macro + screen + score + cache shortlist
        prep_min = open_min - prep_minutes
        prep_hour = open_hour
        if prep_min < 0:
            prep_min += 60
            prep_hour -= 1
        self.scheduler.add_job(
            self.pipeline.pre_market_prep,
            CronTrigger(day_of_week="mon-fri", hour=prep_hour, minute=prep_min, timezone="US/Eastern"),
            id="pre_market",
            name="Pre-market prep",
            misfire_grace_time=300,
        )

        # Full trading cycle: hourly from 10 AM to 3 PM
        self.scheduler.add_job(
            self.pipeline.run_full_cycle,
            CronTrigger(
                day_of_week="mon-fri",
                hour=f"{open_hour + 1}-{close_hour - 1}",
                minute=0,
                timezone="US/Eastern",
            ),
            id="full_cycle",
            name="Full scan-analyze-trade cycle",
            misfire_grace_time=300,
        )

        # Re-rank shortlist: every 10 min from 9:29:50 until market close
        # Fires at :09:50, :19:50, :29:50, :39:50, :49:50, :59:50 each hour
        # Pre-open fires (before shortlist is ready) are no-ops
        rerank_interval = sc.get("rerank_interval_minutes", 10)
        offsets = sorted((rerank_interval * i - 1) % 60 for i in range(1, 60 // rerank_interval + 1))
        minutes = ",".join(str(m) for m in offsets)
        self.scheduler.add_job(
            self.pipeline.run_rerank_cycle,
            CronTrigger(
                day_of_week="mon-fri",
                hour=f"{open_hour}-{close_hour - 1}",
                minute=minutes,
                second=50,
                timezone="US/Eastern",
            ),
            id="rerank",
            name="Re-rank shortlist",
            misfire_grace_time=300,
        )

        # Position monitoring: every 30 seconds
        monitor_interval = sc.get("monitor_interval_seconds", 30)
        self.scheduler.add_job(
            self.monitor.check_positions,
            "interval",
            seconds=monitor_interval,
            id="position_monitor",
            name="Position monitor",
        )

        logger.info("All jobs scheduled:")
        for job in self.scheduler.get_jobs():
            logger.info(f"  - {job.name} ({job.id}): {job.trigger}")

    def start(self):
        """Start the scheduler and block until Ctrl+C."""
        logger.info("Starting trading scheduler...")
        self.setup_jobs()

        # Run pre-market prep immediately on startup
        logger.info("Running initial pre-market prep...")
        self.pipeline.pre_market_prep()

        self.scheduler.start()
        logger.info("Scheduler running. Press Ctrl+C to stop.")
        print("\n  Scheduler running. Press Ctrl+C to stop.\n")

        try:
            while True:
                import time
                time.sleep(1)
        except (KeyboardInterrupt, EOFError):
            pass

        self.shutdown()

    def shutdown(self):
        """Gracefully shut down the scheduler."""
        if self.scheduler.running:
            logger.info("Shutting down scheduler...")
            self.scheduler.shutdown(wait=False)
