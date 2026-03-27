"""Real-time position monitoring."""

import logging

from core.config import Config
from core.database import Database
from core.models import Signal
from executor.alpaca_client import AlpacaClient
from executor.order_manager import OrderManager
from monitor.stop_loss import check_stop_conditions, update_high_water_mark
from monitor.alerts import AlertManager

logger = logging.getLogger("aitrading.monitor.positions")
txn_logger = logging.getLogger("aitrading.transactions")


class PositionMonitor:
    """Monitors open positions for stop-loss, trailing stop, and take-profit."""

    def __init__(
        self,
        config: Config,
        db: Database,
        broker: AlpacaClient,
        order_mgr: OrderManager,
        alerts: AlertManager,
    ):
        self.config = config
        self.db = db
        self.broker = broker
        self.order_mgr = order_mgr
        self.alerts = alerts

    def check_positions(self):
        """
        Check all open positions against stop/trailing/TP conditions.
        Called every 30 seconds by the scheduler.
        """
        if not self.broker.is_market_open():
            return

        positions = self.db.get_open_positions()
        if not positions:
            return

        # Get live position data from Alpaca
        try:
            live_positions = self.broker.get_positions()
        except Exception as e:
            logger.error(f"Failed to fetch live positions: {e}")
            return

        live_map = {p["ticker"]: p for p in live_positions}

        for pos in positions:
            live = live_map.get(pos.ticker)
            if not live:
                continue

            current_price = live["current_price"]

            # Save price snapshot
            self.db.save_price_snapshot(pos.ticker, current_price)

            # Update high-water mark
            if update_high_water_mark(pos, current_price):
                self.db.update_position(
                    pos.id, high_water_mark=pos.high_water_mark
                )

            # Check stop conditions
            signal = check_stop_conditions(pos, current_price, self.config)
            if signal:
                self._execute_exit(pos, signal, current_price)

    def _execute_exit(self, pos, signal: Signal, current_price: float):
        """Execute a stop/TP triggered sell."""
        logger.info(f"Executing exit for {pos.ticker}: {signal.reason}")

        order = self.order_mgr.execute_signal(signal, current_price)

        if order.status != "failed":
            fill_price = order.filled_price or current_price
            self.db.close_position(pos.id, fill_price, signal.reason)
            pnl = (fill_price - pos.entry_price) * pos.qty
            pnl_pct = ((fill_price - pos.entry_price) / pos.entry_price) * 100
            txn_logger.info(
                f"EXIT | {pos.ticker} | qty={pos.qty} | "
                f"entry={pos.entry_price:.2f} | exit={fill_price:.2f} | "
                f"pnl=${pnl:.2f} ({pnl_pct:+.1f}%) | reason={signal.reason}"
            )
            self.alerts.position_closed(
                pos.ticker, pos.qty, fill_price, signal.reason, pnl
            )
            self.alerts.stop_triggered(pos.ticker, signal.reason, current_price)
        else:
            self.alerts.order_failed(pos.ticker, order.error_message)

    def get_portfolio_summary(self) -> dict:
        """Get a summary of the current portfolio state."""
        try:
            account = self.broker.get_account()
            positions = self.broker.get_positions()
            db_positions = self.db.get_open_positions()

            total_pnl = sum(p["unrealized_pnl"] for p in positions)

            return {
                "account_value": account["portfolio_value"],
                "cash": account["cash"],
                "positions_count": len(positions),
                "total_unrealized_pnl": total_pnl,
                "positions": positions,
                "db_positions": len(db_positions),
            }
        except Exception as e:
            logger.error(f"Failed to get portfolio summary: {e}")
            return {}
