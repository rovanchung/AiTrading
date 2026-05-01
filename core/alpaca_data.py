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
    "5d": timedelta(days=7),  # extra margin for weekends
    "1mo": timedelta(days=35),
    "3mo": timedelta(days=100),
    "6mo": timedelta(days=190),
    "1y": timedelta(days=370),
    "2y": timedelta(days=740),
}

# Alpaca's batch bars endpoint silently drops a subset of symbols under load
# (observed during market hours with multiple processes calling concurrently).
# Smaller chunks bound the response size and eliminate the drops.
CHUNK_SIZE = 25


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


def _bars_to_frames(bars_df: pd.DataFrame, tickers: list) -> dict:
    """Convert Alpaca's MultiIndex BarSet df to {ticker: yf-format DataFrame}."""
    if bars_df.empty:
        return {}

    rename = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    bars_df = bars_df.rename(columns=rename)
    keep_cols = ["Open", "High", "Low", "Close", "Volume"]
    bars_df = bars_df[[c for c in keep_cols if c in bars_df.columns]]

    symbols_present = set(bars_df.index.get_level_values("symbol").unique())
    frames = {}
    for ticker in tickers:
        if ticker in symbols_present:
            tdf = bars_df.xs(ticker, level="symbol").copy()
            tdf.index = tdf.index.tz_localize(None)
            tdf.index.name = "Date"
            frames[ticker] = tdf
    return frames


def _fetch_chunk(tickers: list, start) -> dict:
    """Fetch one chunk of tickers from Alpaca and return {ticker: DataFrame}."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    request = StockBarsRequest(
        symbol_or_symbols=tickers,
        timeframe=TimeFrame.Day,
        start=start,
    )
    bars = _get_stock_client().get_stock_bars(request)
    return _bars_to_frames(bars.df, tickers)


def alpaca_download(tickers, period="3mo", group_by=None, **kwargs) -> pd.DataFrame:
    """Download OHLCV data from Alpaca, returning yfinance-compatible DataFrame.

    Symbols are fetched in chunks of CHUNK_SIZE; any tickers missing from the
    chunked responses are retried individually, since Alpaca's batch endpoint
    occasionally drops a subset under load (deterministic ~12-symbol drop
    observed during market hours with three concurrent processes).

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
    if isinstance(tickers, str):
        ticker_list = [tickers]
    else:
        ticker_list = list(tickers)

    # Index tickers not supported — signal caller to use yfinance
    if any(t.startswith("^") for t in ticker_list):
        raise ValueError(
            f"Alpaca does not support index tickers: {[t for t in ticker_list if t.startswith('^')]}"
        )

    start = datetime.now() - PERIOD_MAP.get(period, timedelta(days=100))

    # Chunked batch fetch
    frames = {}
    for i in range(0, len(ticker_list), CHUNK_SIZE):
        chunk = ticker_list[i : i + CHUNK_SIZE]
        try:
            frames.update(_fetch_chunk(chunk, start))
        except Exception as e:
            logger.warning(f"Alpaca chunk fetch failed for {len(chunk)} symbols: {e}")

    # Retry any missing tickers individually
    missing = [t for t in ticker_list if t not in frames]
    if missing:
        logger.info(
            f"Alpaca batch dropped {len(missing)}/{len(ticker_list)} tickers, retrying individually: "
            f"{missing[:10]}{'...' if len(missing) > 10 else ''}"
        )
        for ticker in missing:
            try:
                retry = _fetch_chunk([ticker], start)
                if ticker in retry:
                    frames[ticker] = retry[ticker]
            except Exception as e:
                logger.warning(f"Alpaca individual retry failed for {ticker}: {e}")

    # Single ticker without group_by="ticker": return flat DataFrame
    if len(ticker_list) == 1 and group_by != "ticker":
        return frames.get(ticker_list[0], pd.DataFrame())

    if not frames:
        return pd.DataFrame()

    # Multi-ticker: pivot to MultiIndex columns (ticker, field), preserving request order
    ordered = {t: frames[t] for t in ticker_list if t in frames}
    return pd.concat(ordered, axis=1)


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
        result.append(
            {
                "title": article.headline,
                "link": article.url,
                "publisher": article.source,
                "published": (
                    article.created_at.isoformat() if article.created_at else None
                ),
            }
        )
    return result
