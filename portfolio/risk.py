"""Position sizing and risk calculations."""

import logging

import pandas as pd
import pandas_ta as ta

from core.config import Config

logger = logging.getLogger("aitrading.portfolio.risk")


def calculate_position_size(
    current_price: float,
    account_value: float,
    df: pd.DataFrame,
    config: Config,
) -> int:
    """
    Calculate position size using ATR-based risk management.

    1. Base allocation = account_value * max_position_pct
    2. Risk amount = account_value * risk_per_trade_pct
    3. Stop distance = 2 * ATR (Average True Range)
    4. Max shares from risk = risk_amount / stop_distance
    5. Max shares from allocation = base_allocation / current_price
    6. Final qty = min(risk_shares, allocation_shares)

    Returns number of shares to buy (integer).
    """
    max_pct = config.trading.get("max_position_pct", 0.10)
    risk_pct = config.trading.get("risk_per_trade_pct", 0.02)
    cash_reserve = config.trading.get("cash_reserve_pct", 0.20)

    deployable = account_value * (1.0 - cash_reserve)
    base_allocation = deployable * max_pct
    risk_amount = account_value * risk_pct

    # ATR-based stop distance
    atr = df.ta.atr(length=14)
    if atr is not None and not atr.empty:
        atr_val = atr.iloc[-1]
        stop_distance = 2 * atr_val
    else:
        # Fallback: use configured stop-loss percentage
        stop_loss_pct = config.trading.get("stop_loss_pct", 0.05)
        stop_distance = current_price * stop_loss_pct

    if stop_distance <= 0 or current_price <= 0:
        return 0

    max_shares_risk = int(risk_amount / stop_distance)
    max_shares_alloc = int(base_allocation / current_price)

    qty = max(1, min(max_shares_risk, max_shares_alloc))

    logger.debug(
        f"Position sizing: price=${current_price:.2f}, "
        f"alloc=${base_allocation:.0f}, risk=${risk_amount:.0f}, "
        f"ATR_stop=${stop_distance:.2f}, qty={qty}"
    )
    return qty


def calculate_stop_loss(entry_price: float, config: Config) -> float:
    """Calculate initial stop-loss price."""
    pct = config.trading.get("stop_loss_pct", 0.05)
    return round(entry_price * (1 - pct), 2)


def calculate_take_profit(entry_price: float, config: Config) -> float:
    """Calculate take-profit price."""
    pct = config.trading.get("take_profit_pct", 0.15)
    return round(entry_price * (1 + pct), 2)
