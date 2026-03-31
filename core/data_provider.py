"""Unified data provider — Alpaca (OHLCV/news), FMP (fundamentals), yfinance (fallback)."""

import json
import logging
import time

import yfinance as yf
import pandas as pd
from curl_cffi.requests import Session

logger = logging.getLogger("aitrading.core.data_provider")

# Defaults for yfinance fallback
DEFAULT_TIMEOUT = 10  # seconds per request
DEFAULT_RETRIES = 1   # 1 retry = 2 total attempts

# Track consecutive Alpaca failures to avoid hammering a broken service
_alpaca_fail_count = 0
_ALPACA_FAIL_THRESHOLD = 10  # After this many failures, disable Alpaca until reset

# yfinance rate limiting — minimum gap between consecutive calls
_YF_MIN_DELAY = 0.5  # seconds between yfinance requests
_yf_last_call = 0.0


def _yf_throttle():
    """Enforce minimum delay between yfinance API calls."""
    global _yf_last_call
    elapsed = time.monotonic() - _yf_last_call
    if elapsed < _YF_MIN_DELAY:
        time.sleep(_YF_MIN_DELAY - elapsed)
    _yf_last_call = time.monotonic()


def _make_session(timeout: int) -> Session:
    """Create a curl_cffi session with the given timeout."""
    s = Session()
    s.timeout = timeout
    return s


def _alpaca_available() -> bool:
    """Check if Alpaca should be attempted (not in massive-failure mode)."""
    return _alpaca_fail_count < _ALPACA_FAIL_THRESHOLD


def _record_alpaca_success():
    global _alpaca_fail_count
    _alpaca_fail_count = 0


def _record_alpaca_failure():
    global _alpaca_fail_count
    _alpaca_fail_count += 1
    if _alpaca_fail_count == _ALPACA_FAIL_THRESHOLD:
        logger.warning(
            f"Alpaca hit {_ALPACA_FAIL_THRESHOLD} consecutive failures — "
            "switching to yfinance-only until next success"
        )


def reset_alpaca():
    """Reset Alpaca failure counter (e.g. on new trading session)."""
    global _alpaca_fail_count
    _alpaca_fail_count = 0
    logger.info("Alpaca failure counter reset")


def yf_download(tickers, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, **kwargs):
    """Download OHLCV data — tries Alpaca first, falls back to yfinance.

    Args:
        tickers: Single ticker string or list of tickers.
        timeout: Request timeout in seconds for yfinance fallback.
        retries: Number of retries for yfinance fallback.
        **kwargs: Passed through (period, group_by, etc).
    """
    # --- Try Alpaca first ---
    if _alpaca_available():
        try:
            from core.alpaca_data import alpaca_download
            period = kwargs.get("period", "3mo")
            group_by = kwargs.get("group_by")
            df = alpaca_download(tickers, period=period, group_by=group_by)
            if not df.empty:
                _record_alpaca_success()
                return df
            # Empty result — fall through to yfinance
        except ValueError:
            # Index tickers (^VIX etc.) — expected, go straight to yfinance
            pass
        except Exception as e:
            _record_alpaca_failure()
            logger.debug(f"Alpaca download failed for {_ticker_label(tickers)}, falling back to yfinance: {e}")

    # --- yfinance fallback ---
    kwargs.setdefault("progress", False)
    kwargs["timeout"] = timeout

    last_err = None
    for attempt in range(1 + retries):
        try:
            _yf_throttle()
            return yf.download(tickers, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < retries:
                logger.debug(f"yf.download retry {attempt + 1} for {_ticker_label(tickers)}: {e}")
                time.sleep(1)

    logger.warning(f"yf.download failed for {_ticker_label(tickers)} after {1 + retries} attempts: {last_err}")
    return pd.DataFrame()


def yf_ticker_info(ticker: str, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES) -> dict:
    """Fetch fundamental data — tries FMP first, falls back to yfinance.

    Returns empty dict on failure.
    """
    # --- Try FMP first ---
    try:
        from core.fmp_data import fmp_ticker_info
        info = fmp_ticker_info(ticker)
        if info:
            return info
    except Exception as e:
        logger.debug(f"FMP info failed for {ticker}, falling back to yfinance: {e}")

    # --- yfinance fallback ---
    last_err = None
    for attempt in range(1 + retries):
        try:
            _yf_throttle()
            t = yf.Ticker(ticker, session=_make_session(timeout))
            return t.info or {}
        except Exception as e:
            last_err = e
            if attempt < retries:
                logger.debug(f"yf.Ticker.info retry {attempt + 1} for {ticker}: {e}")
                time.sleep(2)

    logger.warning(f"yf.Ticker.info failed for {ticker} after {1 + retries} attempts: {last_err}")
    return {}


def yf_ticker_news(ticker: str, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES) -> list:
    """Fetch news — tries Alpaca first, falls back to yfinance.

    Returns empty list on failure.
    """
    # --- Try Alpaca first ---
    if _alpaca_available():
        try:
            from core.alpaca_data import alpaca_news
            news = alpaca_news(ticker)
            if news:
                _record_alpaca_success()
                return news
        except Exception as e:
            _record_alpaca_failure()
            logger.debug(f"Alpaca news failed for {ticker}, falling back to yfinance: {e}")

    # --- yfinance fallback ---
    last_err = None
    for attempt in range(1 + retries):
        try:
            _yf_throttle()
            t = yf.Ticker(ticker, session=_make_session(timeout))
            return t.news or []
        except Exception as e:
            last_err = e
            if attempt < retries:
                logger.debug(f"yf.Ticker.news retry {attempt + 1} for {ticker}: {e}")
                time.sleep(2)

    logger.warning(f"yf.Ticker.news failed for {ticker} after {1 + retries} attempts: {last_err}")
    return []


def fetch_fundamentals(ticker: str) -> tuple[dict, str, str] | None:
    """Fetch fundamental data from provider chain: Finnhub -> FMP -> yfinance.

    Returns (normalized_data_dict, provider_name, raw_json) or None if all fail.
    Dict keys match fundamentals DB columns. All values normalized to decimals.
    """
    # --- Finnhub (primary) ---
    try:
        from core.finnhub_data import finnhub_fundamentals
        data, raw_json = finnhub_fundamentals(ticker)
        if data:
            logger.info(f"Fundamentals for {ticker}: Finnhub OK ({len(data)} fields)")
            return data, "finnhub", raw_json
        logger.debug(f"Finnhub returned no data for {ticker}")
    except ValueError as e:
        # Missing API key — log once clearly, skip to fallback
        logger.warning(f"Finnhub fundamentals skipped for {ticker}: {e}")
    except Exception as e:
        logger.warning(
            f"Finnhub fundamentals failed for {ticker}: "
            f"{type(e).__name__}: {e}"
        )

    # --- FMP fallback ---
    try:
        from core.fmp_data import fmp_ticker_info
        info = fmp_ticker_info(ticker)
        if info:
            data = _normalize_fmp_to_db(info)
            if data:
                logger.info(
                    f"Fundamentals for {ticker}: FMP fallback OK ({len(data)} fields)"
                )
                return data, "fmp", json.dumps(info)
    except Exception as e:
        logger.warning(
            f"FMP fundamentals failed for {ticker}: "
            f"{type(e).__name__}: {e}"
        )

    # --- yfinance last resort ---
    try:
        _yf_throttle()
        t = yf.Ticker(ticker, session=_make_session(DEFAULT_TIMEOUT))
        info = t.info or {}
        if info:
            data = _normalize_yfinance_to_db(info)
            if data:
                logger.info(
                    f"Fundamentals for {ticker}: yfinance fallback OK ({len(data)} fields)"
                )
                return data, "yfinance", json.dumps(info)
    except Exception as e:
        logger.warning(
            f"yfinance fundamentals failed for {ticker}: "
            f"{type(e).__name__}: {e}"
        )

    logger.error(f"All fundamental providers failed for {ticker}")
    return None


def _normalize_fmp_to_db(info: dict) -> dict:
    """Convert FMP yfinance-compatible dict to DB column names.

    FMP returns values already converted by fmp_data.py:
    - ROE/margins as decimals (0.15 = 15%)
    - debtToEquity as percentage (103 = 103%) — convert back to ratio
    """
    data = {}
    if info.get("returnOnEquity") is not None:
        data["roe_ttm"] = info["returnOnEquity"]
    if info.get("profitMargins") is not None:
        data["net_margin_ttm"] = info["profitMargins"]
    if info.get("currentRatio") is not None:
        data["current_ratio_quarterly"] = info["currentRatio"]
    if info.get("debtToEquity") is not None:
        data["debt_to_equity_annual"] = info["debtToEquity"] / 100.0  # pct -> ratio
    if info.get("freeCashflow") is not None:
        data["free_cash_flow_ttm"] = info["freeCashflow"]
    if info.get("pegRatio") is not None:
        # PEG is price-dependent but we can back-derive earnings growth if EPS is known
        pass  # Skip — PEG computed at runtime
    return data


def _normalize_yfinance_to_db(info: dict) -> dict:
    """Convert yfinance Ticker.info dict to DB column names."""
    data = {}
    if info.get("trailingEps") is not None:
        data["eps_ttm"] = info["trailingEps"]
    if info.get("forwardEps") is not None:
        data["eps_annual"] = info["forwardEps"]
    if info.get("bookValue") is not None:
        data["book_value_per_share_quarterly"] = info["bookValue"]
    if info.get("returnOnEquity") is not None:
        data["roe_ttm"] = info["returnOnEquity"]  # decimal
    if info.get("profitMargins") is not None:
        data["net_margin_ttm"] = info["profitMargins"]  # decimal
    if info.get("grossMargins") is not None:
        data["gross_margin_ttm"] = info["grossMargins"]  # decimal
    if info.get("operatingMargins") is not None:
        data["operating_margin_ttm"] = info["operatingMargins"]  # decimal
    if info.get("revenueGrowth") is not None:
        data["revenue_growth_ttm_yoy"] = info["revenueGrowth"]  # decimal
    if info.get("earningsGrowth") is not None:
        data["earnings_growth_ttm"] = info["earningsGrowth"]  # decimal
    if info.get("currentRatio") is not None:
        data["current_ratio_quarterly"] = info["currentRatio"]
    if info.get("debtToEquity") is not None:
        data["debt_to_equity_annual"] = info["debtToEquity"] / 100.0  # pct -> ratio
    if info.get("freeCashflow") is not None:
        data["free_cash_flow_ttm"] = info["freeCashflow"]
    return data


def _ticker_label(tickers) -> str:
    """Format tickers for log messages."""
    if isinstance(tickers, str):
        return tickers
    if isinstance(tickers, (list, tuple)):
        if len(tickers) <= 3:
            return ", ".join(tickers)
        return f"{tickers[0]}...{tickers[-1]} ({len(tickers)} tickers)"
    return str(tickers)
