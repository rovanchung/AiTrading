"""Alpaca market data provider — primary data source.

Provides OHLCV bars and news in yfinance-compatible format so callers
don't need to change.  Index tickers (^VIX, ^TNX, etc.) are not
supported by Alpaca and will raise ValueError so the caller can
fall back to yfinance.
"""

import logging
import os
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger("aitrading.core.alpaca_data")

# Lazy-initialized clients
_stock_client = None
_news_client = None

PERIOD_MAP = {
    "1d": timedelta(days=1),
    "5d": timedelta(days=7),      # extra margin for weekends
    "1mo": timedelta(days=35),
    "3mo": timedelta(days=100),
    "6mo": timedelta(days=190),
    "1y": timedelta(days=370),
    "2y": timedelta(days=740),
}


def _get_stock_client():
    global _stock_client
    if _stock_client is None:
        from alpaca.data.historical import StockHistoricalDataClient
        _stock_client = StockHistoricalDataClient(
            api_key=os.environ.get("ALPACA_API_KEY"),
            secret_key=os.environ.get("ALPACA_SECRET_KEY"),
        )
    return _stock_client


def _get_news_client():
    global _news_client
    if _news_client is None:
        from alpaca.data.historical import NewsClient
        _news_client = NewsClient(
            api_key=os.environ.get("ALPACA_API_KEY"),
            secret_key=os.environ.get("ALPACA_SECRET_KEY"),
        )
    return _news_client


def alpaca_download(tickers, period="3mo", group_by=None, **kwargs) -> pd.DataFrame:
    """Download OHLCV data from Alpaca, returning yfinance-compatible DataFrame.

    Args:
        tickers: Single ticker string or list of tickers.
        period: yfinance-style period string (e.g. "5d", "3mo", "1y").
        group_by: If "ticker", returns MultiIndex columns for multi-ticker.
        **kwargs: Ignored (absorbs yfinance-specific args like threads, timeout).

    Returns:
        DataFrame with columns Open, High, Low, Close, Volume and DatetimeIndex.
        For multiple tickers with group_by="ticker": MultiIndex columns (ticker, field).

    Raises:
        ValueError: If any ticker is an index (starts with ^).
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    if isinstance(tickers, str):
        ticker_list = [tickers]
    else:
        ticker_list = list(tickers)

    # Index tickers not supported — signal caller to use yfinance
    if any(t.startswith("^") for t in ticker_list):
        raise ValueError(f"Alpaca does not support index tickers: {[t for t in ticker_list if t.startswith('^')]}")

    start = datetime.now() - PERIOD_MAP.get(period, timedelta(days=100))

    request = StockBarsRequest(
        symbol_or_symbols=ticker_list,
        timeframe=TimeFrame.Day,
        start=start,
    )

    bars = _get_stock_client().get_stock_bars(request)
    df = bars.df

    if df.empty:
        return pd.DataFrame()

    # Alpaca returns MultiIndex (symbol, timestamp) with lowercase columns.
    # Convert to yfinance format.
    rename = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    df = df.rename(columns=rename)
    keep_cols = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in keep_cols if c in df.columns]]

    if len(ticker_list) == 1 and group_by != "ticker":
        # Single ticker: drop symbol level, keep timestamp as index
        ticker = ticker_list[0]
        if ticker in df.index.get_level_values("symbol"):
            df = df.xs(ticker, level="symbol")
        df.index = df.index.tz_localize(None)
        df.index.name = "Date"
        return df

    # Multiple tickers: pivot to MultiIndex columns (ticker, field)
    frames = {}
    for ticker in ticker_list:
        if ticker in df.index.get_level_values("symbol"):
            tdf = df.xs(ticker, level="symbol").copy()
            tdf.index = tdf.index.tz_localize(None)
            tdf.index.name = "Date"
            frames[ticker] = tdf

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, axis=1)
    return combined


def alpaca_news(ticker: str, limit: int = 20) -> list:
    """Fetch news from Alpaca, returning yfinance-compatible list of dicts.

    Each dict has keys: title, link, publisher, published.
    """
    from alpaca.data.requests import NewsRequest

    client = _get_news_client()
    request = NewsRequest(symbols=ticker, limit=limit)
    news_set = client.get_news(request)

    articles = news_set.data.get("news", []) if news_set.data else []
    result = []
    for article in articles:
        result.append({
            "title": article.headline,
            "link": article.url,
            "publisher": article.source,
            "published": article.created_at.isoformat() if article.created_at else None,
        })
    return result
