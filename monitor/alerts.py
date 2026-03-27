"""Alert system for trading events."""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("aitrading.monitor.alerts")

LEVEL_INFO = "INFO"
LEVEL_WARNING = "WARNING"
LEVEL_CRITICAL = "CRITICAL"


class AlertManager:
    """Manages trading alerts via logging and optional JSON file."""

    def __init__(self, alerts_file: str = "data/logs/alerts.json"):
        self.alerts_file = Path(alerts_file)
        self.alerts_file.parent.mkdir(parents=True, exist_ok=True)

    def alert(self, level: str, title: str, message: str, data: dict = None):
        """Fire an alert."""
        alert_record = {
            "time": datetime.now().isoformat(),
            "level": level,
            "title": title,
            "message": message,
            "data": data or {},
        }

        # Log it
        log_msg = f"[{level}] {title}: {message}"
        if level == LEVEL_CRITICAL:
            logger.critical(log_msg)
        elif level == LEVEL_WARNING:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        # Append to alerts file
        self._write_alert(alert_record)

    def position_opened(self, ticker: str, qty: int, price: float):
        self.alert(LEVEL_INFO, "Position Opened",
                   f"Bought {qty} shares of {ticker} @ ${price:.2f}",
                   {"ticker": ticker, "qty": qty, "price": price})

    def position_closed(self, ticker: str, qty: int, price: float, reason: str, pnl: float):
        level = LEVEL_INFO if pnl >= 0 else LEVEL_WARNING
        self.alert(level, "Position Closed",
                   f"Sold {qty} shares of {ticker} @ ${price:.2f} ({reason}), PnL: ${pnl:.2f}",
                   {"ticker": ticker, "qty": qty, "price": price, "reason": reason, "pnl": pnl})

    def stop_triggered(self, ticker: str, stop_type: str, price: float):
        self.alert(LEVEL_WARNING, f"{stop_type} Triggered",
                   f"{ticker} @ ${price:.2f}",
                   {"ticker": ticker, "type": stop_type, "price": price})

    def drawdown_alert(self, drawdown_pct: float, action: str):
        self.alert(LEVEL_CRITICAL, "Drawdown Alert",
                   f"Portfolio drawdown {drawdown_pct:.1%} — {action}",
                   {"drawdown_pct": drawdown_pct, "action": action})

    def order_failed(self, ticker: str, error: str):
        self.alert(LEVEL_CRITICAL, "Order Failed",
                   f"Failed to execute order for {ticker}: {error}",
                   {"ticker": ticker, "error": error})

    def _write_alert(self, record: dict):
        try:
            with open(self.alerts_file, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"Failed to write alert to file: {e}")
