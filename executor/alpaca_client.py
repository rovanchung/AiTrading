"""Alpaca broker API wrapper."""

import logging
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    GetAssetsRequest,
    LimitOrderRequest,
    MarketOrderRequest,
)
from alpaca.trading.enums import AssetClass, OrderSide, TimeInForce

from core.config import Config
from core.exceptions import BrokerError

logger = logging.getLogger("aitrading.executor.alpaca")


class AlpacaClient:
    """Wrapper around Alpaca Trading API."""

    def __init__(self, config: Config):
        self.config = config
        paper = config.trading.get("paper_trading", True)
        self.client = TradingClient(
            api_key=config.alpaca_api_key,
            secret_key=config.alpaca_secret_key,
            paper=paper,
        )
        logger.info(f"Alpaca client initialized (paper={paper})")

    def get_account(self) -> dict:
        """Get account info: equity, cash, buying power."""
        try:
            acct = self.client.get_account()
            return {
                "equity": float(acct.equity),
                "cash": float(acct.cash),
                "buying_power": float(acct.buying_power),
                "portfolio_value": float(acct.portfolio_value),
                "status": acct.status,
            }
        except Exception as e:
            raise BrokerError(f"Failed to get account: {e}") from e

    def get_positions(self) -> list[dict]:
        """Get all open positions from Alpaca."""
        try:
            positions = self.client.get_all_positions()
            return [
                {
                    "ticker": p.symbol,
                    "qty": int(p.qty),
                    "avg_entry": float(p.avg_entry_price),
                    "current_price": float(p.current_price),
                    "market_value": float(p.market_value),
                    "unrealized_pnl": float(p.unrealized_pl),
                    "unrealized_pnl_pct": float(p.unrealized_plpc),
                }
                for p in positions
            ]
        except Exception as e:
            raise BrokerError(f"Failed to get positions: {e}") from e

    def submit_market_order(
        self, ticker: str, qty: int, side: str
    ) -> dict:
        """Submit a market order (used for sells for immediate execution)."""
        try:
            order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
            req = MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
            )
            order = self.client.submit_order(req)
            logger.info(f"Market order submitted: {side} {qty} {ticker} -> {order.id}")
            return {
                "order_id": str(order.id),
                "status": str(order.status),
                "filled_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            }
        except Exception as e:
            raise BrokerError(f"Market order failed for {ticker}: {e}") from e

    def submit_limit_order(
        self, ticker: str, qty: int, side: str, limit_price: float
    ) -> dict:
        """Submit a limit order (used for buys to control entry price)."""
        try:
            order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
            req = LimitOrderRequest(
                symbol=ticker,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 2),
            )
            order = self.client.submit_order(req)
            logger.info(
                f"Limit order submitted: {side} {qty} {ticker} @ ${limit_price:.2f} -> {order.id}"
            )
            return {
                "order_id": str(order.id),
                "status": str(order.status),
                "limit_price": limit_price,
            }
        except Exception as e:
            raise BrokerError(f"Limit order failed for {ticker}: {e}") from e

    def get_order(self, order_id: str) -> dict:
        """Check status of an existing order."""
        try:
            order = self.client.get_order_by_id(order_id)
            return {
                "order_id": str(order.id),
                "status": str(order.status),
                "filled_qty": int(order.filled_qty) if order.filled_qty else 0,
                "filled_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            }
        except Exception as e:
            raise BrokerError(f"Failed to get order {order_id}: {e}") from e

    def cancel_order(self, order_id: str):
        """Cancel an open order."""
        try:
            self.client.cancel_order_by_id(order_id)
            logger.info(f"Order cancelled: {order_id}")
        except Exception as e:
            raise BrokerError(f"Failed to cancel order {order_id}: {e}") from e

    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        try:
            clock = self.client.get_clock()
            return clock.is_open
        except Exception as e:
            logger.error(f"Failed to check market clock: {e}")
            return False
