"""Order management — submission, tracking, and retry logic."""

import logging
import time
from datetime import datetime

from core.config import Config
from core.database import Database
from core.models import Order, Signal
from core.exceptions import OrderError, BrokerError
from executor.alpaca_client import AlpacaClient

logger = logging.getLogger("aitrading.executor.orders")

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


class OrderManager:
    """Manages order lifecycle: create, submit, track, retry."""

    def __init__(self, config: Config, db: Database, broker: AlpacaClient):
        self.config = config
        self.db = db
        self.broker = broker

    def execute_signal(self, signal: Signal, current_price: float) -> Order:
        """Execute a buy or sell signal, returns the Order record."""
        order = Order(
            ticker=signal.ticker,
            side=signal.action,
            qty=signal.suggested_qty,
            submitted_at=datetime.now(),
        )

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if signal.action == "buy":
                    # Use limit order slightly above current price to ensure fill
                    limit_price = round(current_price * 1.001, 2)
                    result = self.broker.submit_limit_order(
                        signal.ticker, signal.suggested_qty, "buy", limit_price
                    )
                    order.order_type = "limit"
                    order.limit_price = limit_price
                else:
                    # Use market order for sells (immediate exit)
                    result = self.broker.submit_market_order(
                        signal.ticker, signal.suggested_qty, "sell"
                    )
                    order.order_type = "market"

                order.alpaca_order_id = result["order_id"]
                order.status = result["status"]
                order.filled_price = result.get("filled_price")
                if order.filled_price:
                    order.filled_at = datetime.now()

                logger.info(
                    f"Order executed: {signal.action} {signal.suggested_qty} "
                    f"{signal.ticker} (attempt {attempt}) -> {order.status}"
                )
                break

            except BrokerError as e:
                order.error_message = str(e)
                logger.warning(
                    f"Order attempt {attempt}/{MAX_RETRIES} failed for "
                    f"{signal.ticker}: {e}"
                )
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                else:
                    order.status = "failed"
                    logger.error(f"Order permanently failed: {signal.ticker}")

        # Save to database
        order.id = self.db.save_order(order)
        return order

    def check_order_status(self, order: Order) -> Order:
        """Poll Alpaca for updated order status."""
        if not order.alpaca_order_id or order.status in ("filled", "failed"):
            return order

        try:
            result = self.broker.get_order(order.alpaca_order_id)
            order.status = result["status"]
            if result["filled_price"]:
                order.filled_price = result["filled_price"]
                order.filled_at = datetime.now()

            self.db.update_order(
                order.id,
                status=order.status,
                filled_price=order.filled_price,
                filled_at=order.filled_at,
            )
        except BrokerError as e:
            logger.error(f"Failed to check order {order.alpaca_order_id}: {e}")

        return order
