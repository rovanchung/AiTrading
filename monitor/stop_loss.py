"""Stop-loss, trailing stop, and take-profit logic."""

import logging

from core.config import Config
from core.models import Position, Signal

logger = logging.getLogger("aitrading.monitor.stoploss")


def check_stop_conditions(
    position: Position,
    current_price: float,
    config: Config,
) -> Signal | None:
    """
    Check if any stop/take-profit condition is triggered.
    Returns a sell Signal if triggered, None otherwise.
    """
    # Hard stop-loss
    if current_price <= position.stop_loss:
        logger.warning(
            f"STOP-LOSS triggered for {position.ticker}: "
            f"${current_price:.2f} <= ${position.stop_loss:.2f}"
        )
        return Signal(
            ticker=position.ticker,
            action="sell",
            reason=f"stop_loss (${current_price:.2f} <= ${position.stop_loss:.2f})",
            suggested_qty=position.qty,
        )

    # Take-profit
    if position.take_profit > 0 and current_price >= position.take_profit:
        logger.info(
            f"TAKE-PROFIT triggered for {position.ticker}: "
            f"${current_price:.2f} >= ${position.take_profit:.2f}"
        )
        return Signal(
            ticker=position.ticker,
            action="sell",
            reason=f"take_profit (${current_price:.2f} >= ${position.take_profit:.2f})",
            suggested_qty=position.qty,
        )

    # Trailing stop: check if price dropped from high-water mark
    trailing_pct = config.trading.get("trailing_stop_pct", 0.03)
    if position.high_water_mark > 0:
        trailing_stop_price = position.high_water_mark * (1 - trailing_pct)
        if current_price <= trailing_stop_price:
            logger.warning(
                f"TRAILING STOP triggered for {position.ticker}: "
                f"${current_price:.2f} <= ${trailing_stop_price:.2f} "
                f"(HWM: ${position.high_water_mark:.2f})"
            )
            return Signal(
                ticker=position.ticker,
                action="sell",
                reason=f"trailing_stop (${current_price:.2f}, HWM=${position.high_water_mark:.2f})",
                suggested_qty=position.qty,
            )

    return None


def update_high_water_mark(position: Position, current_price: float) -> bool:
    """Update high-water mark if current price is higher. Returns True if updated."""
    if current_price > position.high_water_mark:
        position.high_water_mark = current_price
        return True
    return False
