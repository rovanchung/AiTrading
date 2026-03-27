"""Stock screening filters applied to OHLCV data."""

import logging

import pandas as pd

logger = logging.getLogger("aitrading.screener.filters")


def filter_price(df: pd.DataFrame, min_price: float = 5.0, max_price: float = 500.0) -> pd.DataFrame:
    """Exclude penny stocks and extremely expensive stocks."""
    last_close = df.groupby(level=0).last()["Close"] if isinstance(df.index, pd.MultiIndex) else df["Close"]
    mask = (last_close >= min_price) & (last_close <= max_price)
    passed = mask[mask].index.tolist()
    logger.debug(f"Price filter: {len(passed)} passed (range ${min_price}-${max_price})")
    return passed


def filter_volume(data: dict[str, pd.DataFrame], min_avg_volume: int = 500_000) -> list[str]:
    """Minimum 20-day average volume for liquidity."""
    passed = []
    for ticker, df in data.items():
        if df.empty or "Volume" not in df.columns:
            continue
        avg_vol = df["Volume"].tail(20).mean()
        if avg_vol >= min_avg_volume:
            passed.append(ticker)
    logger.debug(f"Volume filter: {len(passed)} passed (min avg {min_avg_volume:,})")
    return passed


def filter_moving_average(data: dict[str, pd.DataFrame]) -> list[str]:
    """Price must be above 50-day SMA (uptrend confirmation)."""
    passed = []
    for ticker, df in data.items():
        if df.empty or len(df) < 50:
            continue
        sma50 = df["Close"].rolling(50).mean().iloc[-1]
        if df["Close"].iloc[-1] > sma50:
            passed.append(ticker)
    logger.debug(f"MA filter: {len(passed)} passed (price > SMA50)")
    return passed


def filter_relative_strength(data: dict[str, pd.DataFrame], spy_df: pd.DataFrame) -> list[str]:
    """Stock must outperform SPY over the last month."""
    passed = []
    if spy_df.empty or len(spy_df) < 20:
        return list(data.keys())  # Skip filter if no SPY data

    spy_return = (spy_df["Close"].iloc[-1] / spy_df["Close"].iloc[-20] - 1) if len(spy_df) >= 20 else 0

    for ticker, df in data.items():
        if df.empty or len(df) < 20:
            continue
        stock_return = df["Close"].iloc[-1] / df["Close"].iloc[-20] - 1
        if stock_return > spy_return:
            passed.append(ticker)
    logger.debug(f"RS filter: {len(passed)} passed (outperforming SPY)")
    return passed
