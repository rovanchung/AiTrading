"""Data models for AiTrading."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Stock:
    """A stock in the universe."""
    ticker: str
    name: str = ""
    sector: str = ""
    market_cap: float = 0.0


@dataclass
class ScoreResult:
    """Multi-dimensional analysis score for a stock."""
    ticker: str
    technical: float = 0.0
    fundamental: float = 0.0
    momentum: float = 0.0
    sentiment: float = 0.0
    composite: float = 0.0
    details: dict = field(default_factory=dict)
    scored_at: datetime = field(default_factory=datetime.now)


@dataclass
class Position:
    """An open or closed trading position."""
    id: Optional[int] = None
    ticker: str = ""
    qty: int = 0
    entry_price: float = 0.0
    entry_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    stop_loss: float = 0.0
    take_profit: float = 0.0
    high_water_mark: float = 0.0
    status: str = "open"  # open, closed
    exit_reason: str = ""
    pnl: float = 0.0
    sector: str = ""

    @property
    def hold_days(self) -> int:
        if self.entry_time is None:
            return 0
        end = self.exit_time or datetime.now()
        return (end - self.entry_time).days

    @property
    def current_value(self) -> float:
        return self.qty * self.entry_price

    def unrealized_pnl_pct(self, current_price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        return (current_price - self.entry_price) / self.entry_price


@dataclass
class Order:
    """A trade order."""
    id: Optional[int] = None
    alpaca_order_id: str = ""
    ticker: str = ""
    side: str = ""  # buy, sell
    qty: int = 0
    order_type: str = "market"  # market, limit
    limit_price: Optional[float] = None
    status: str = "pending"  # pending, submitted, filled, failed, cancelled
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    filled_price: Optional[float] = None
    error_message: str = ""


@dataclass
class Signal:
    """A buy or sell signal from the portfolio manager."""
    ticker: str
    action: str  # buy, sell
    reason: str = ""
    score: float = 0.0
    suggested_qty: int = 0
    stop_loss: float = 0.0
    take_profit: float = 0.0
