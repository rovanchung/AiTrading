"""Custom exception hierarchy for AiTrading."""


class AiTradingError(Exception):
    """Base exception for all AiTrading errors."""


class ConfigError(AiTradingError):
    """Configuration loading or validation error."""


class DataFetchError(AiTradingError):
    """Failed to fetch market data."""


class BrokerError(AiTradingError):
    """Broker API communication error."""


class OrderError(BrokerError):
    """Order submission or execution error."""


class InsufficientFundsError(OrderError):
    """Not enough buying power for the order."""


class RiskLimitError(AiTradingError):
    """Trade would violate risk management rules."""


class DatabaseError(AiTradingError):
    """Database operation error."""
