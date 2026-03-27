"""Portfolio allocation rules and constraints."""

import logging
from collections import Counter

from core.config import Config
from core.models import Position

logger = logging.getLogger("aitrading.portfolio.allocation")


def get_open_slots(positions: list[Position], config: Config) -> int:
    """How many new positions can we open?"""
    max_pos = config.trading.get("max_positions", 10)
    return max(0, max_pos - len(positions))


def check_sector_limit(
    sector: str, positions: list[Position], config: Config,
    sector_limit_override: float = None,
) -> bool:
    """Check if adding a position in this sector would violate sector limits."""
    max_sector_pct = sector_limit_override or config.trading.get("max_sector_pct", 0.30)
    max_pos = config.trading.get("max_positions", 10)

    sector_counts = Counter(p.sector for p in positions)
    current_in_sector = sector_counts.get(sector, 0)

    # Sector limit as fraction of max positions
    max_in_sector = int(max_pos * max_sector_pct)
    if current_in_sector >= max_in_sector:
        logger.info(
            f"Sector limit reached for {sector}: "
            f"{current_in_sector}/{max_in_sector}"
        )
        return False
    return True


def check_cash_reserve(
    cash: float, order_cost: float, account_value: float, config: Config
) -> bool:
    """Ensure we maintain the cash reserve after this purchase."""
    reserve_pct = config.trading.get("cash_reserve_pct", 0.20)
    min_cash = account_value * reserve_pct
    remaining = cash - order_cost
    if remaining < min_cash:
        logger.info(
            f"Cash reserve violation: ${remaining:.0f} < ${min_cash:.0f} required"
        )
        return False
    return True
