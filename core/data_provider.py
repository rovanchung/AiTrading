"""yfinance helpers with timeout and retry."""

import logging
import time

import yfinance as yf
import pandas as pd
from curl_cffi.requests import Session

logger = logging.getLogger("aitrading.core.yf_helpers")

# Defaults
DEFAULT_TIMEOUT = 10  # seconds per request
DEFAULT_RETRIES = 1   # 1 retry = 2 total attempts


def _make_session(timeout: int) -> Session:
    """Create a curl_cffi session with the given timeout."""
    s = Session()
    s.timeout = timeout
    return s


def yf_download(tickers, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, **kwargs):
    """Wrapper around yf.download with timeout and retry.

    Args:
        tickers: Single ticker string or list of tickers.
        timeout: Request timeout in seconds (default 10).
        retries: Number of retries on failure (default 1).
        **kwargs: Passed through to yf.download (period, group_by, etc).
    """
    kwargs.setdefault("progress", False)
    kwargs["timeout"] = timeout

    last_err = None
    for attempt in range(1 + retries):
        try:
            return yf.download(tickers, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < retries:
                logger.debug(f"yf.download retry {attempt + 1} for {_ticker_label(tickers)}: {e}")
                time.sleep(1)

    logger.warning(f"yf.download failed for {_ticker_label(tickers)} after {1 + retries} attempts: {last_err}")
    return pd.DataFrame()


def yf_ticker_info(ticker: str, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES) -> dict:
    """Fetch yf.Ticker(ticker).info with timeout and retry.

    Returns empty dict on failure.
    """
    last_err = None
    for attempt in range(1 + retries):
        try:
            t = yf.Ticker(ticker, session=_make_session(timeout))
            return t.info or {}
        except Exception as e:
            last_err = e
            if attempt < retries:
                logger.debug(f"yf.Ticker.info retry {attempt + 1} for {ticker}: {e}")
                time.sleep(1)

    logger.warning(f"yf.Ticker.info failed for {ticker} after {1 + retries} attempts: {last_err}")
    return {}


def yf_ticker_news(ticker: str, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES) -> list:
    """Fetch yf.Ticker(ticker).news with timeout and retry.

    Returns empty list on failure.
    """
    last_err = None
    for attempt in range(1 + retries):
        try:
            t = yf.Ticker(ticker, session=_make_session(timeout))
            return t.news or []
        except Exception as e:
            last_err = e
            if attempt < retries:
                logger.debug(f"yf.Ticker.news retry {attempt + 1} for {ticker}: {e}")
                time.sleep(1)

    logger.warning(f"yf.Ticker.news failed for {ticker} after {1 + retries} attempts: {last_err}")
    return []


def _ticker_label(tickers) -> str:
    """Format tickers for log messages."""
    if isinstance(tickers, str):
        return tickers
    if isinstance(tickers, (list, tuple)):
        if len(tickers) <= 3:
            return ", ".join(tickers)
        return f"{tickers[0]}...{tickers[-1]} ({len(tickers)} tickers)"
    return str(tickers)
