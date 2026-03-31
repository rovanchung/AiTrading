"""Financial Modeling Prep (FMP) data provider — fundamental data.

Returns ticker info dicts with keys matching yfinance .info format
so callers don't need to change.

Free tier: 250 requests/day. Uses 2 API calls per ticker (ratios-ttm +
key-metrics-ttm) to stay within budget. Results are cached for 24 hours
in a local JSON file so subsequent scans cost zero API calls.
"""

import json
import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger("aitrading.core.fmp_data")

_BASE = "https://financialmodelingprep.com/stable"
_TIMEOUT = 10
_CACHE_TTL = 86400  # 24 hours in seconds
_CACHE_PATH = Path(__file__).parent.parent / "data" / "fmp_cache.json"

# In-memory cache loaded from disk
_cache = {}
_cache_loaded = False


def _load_cache():
    """Load cache from disk once."""
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    if _CACHE_PATH.exists():
        try:
            with open(_CACHE_PATH) as f:
                _cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            _cache = {}


def _save_cache():
    """Persist cache to disk."""
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_PATH, "w") as f:
        json.dump(_cache, f)


def _get_cached(ticker: str) -> dict | None:
    """Return cached info if fresh (< 24h old), else None."""
    _load_cache()
    entry = _cache.get(ticker)
    if entry and (time.time() - entry.get("_ts", 0)) < _CACHE_TTL:
        return {k: v for k, v in entry.items() if k != "_ts"}
    return None


def _set_cached(ticker: str, info: dict):
    """Store info in cache with timestamp."""
    _load_cache()
    _cache[ticker] = {**info, "_ts": time.time()}
    _save_cache()


def _api_key() -> str:
    key = os.environ.get("FMP_API_KEY", "")
    if not key:
        raise ValueError("FMP_API_KEY not set in environment")
    return key


def _get(endpoint: str, params: dict) -> list | dict:
    """Make FMP API request, return parsed JSON."""
    params["apikey"] = _api_key()
    ticker = params.get("symbol", "?")
    try:
        r = requests.get(f"{_BASE}/{endpoint}", params=params, timeout=_TIMEOUT)
        if r.status_code == 402:
            body = r.text[:100].lower()
            if "premium" in body or "special endpoint" in body:
                logger.debug(f"FMP premium-only ticker {ticker} on {endpoint}, falling back")
            else:
                logger.warning(f"FMP daily limit reached (402) on {endpoint} for {ticker}")
            raise requests.HTTPError(f"FMP 402: {r.text[:100]}", response=r)
        if r.status_code == 403:
            logger.warning(f"FMP access denied (403) on {endpoint} for {ticker}")
            raise requests.HTTPError(f"FMP access denied", response=r)
        r.raise_for_status()
        return r.json()
    except requests.Timeout:
        logger.warning(f"FMP timeout on {endpoint} for {ticker}")
        raise
    except requests.ConnectionError:
        logger.warning(f"FMP connection error on {endpoint} for {ticker}")
        raise


def fmp_ticker_info(ticker: str) -> dict:
    """Fetch fundamental data from FMP, returning yfinance-compatible dict.

    Results are cached for 24 hours to stay within the free tier limit.
    Combines ratios-ttm, key-metrics-ttm, and financial-growth into a
    single dict with yfinance key names.
    """
    # Check cache first
    cached = _get_cached(ticker)
    if cached:
        return cached

    info = {}

    # Call 1: ratios-ttm (PE, PEG, PB, margins, debt ratios, FCF per share)
    ratios = _get("ratios-ttm", {"symbol": ticker})
    if isinstance(ratios, list) and ratios:
        r = ratios[0]
        info["trailingPE"] = r.get("priceToEarningsRatioTTM")
        info["pegRatio"] = r.get("priceToEarningsGrowthRatioTTM")
        info["priceToBook"] = r.get("priceToBookRatioTTM")
        info["profitMargins"] = r.get("netProfitMarginTTM")
        info["currentRatio"] = r.get("currentRatioTTM")
        # FMP debtToEquityRatio is a ratio (e.g. 1.03); yfinance uses percentage (e.g. 103)
        de = r.get("debtToEquityRatioTTM")
        if de is not None:
            info["debtToEquity"] = de * 100
        fcf_per_share = r.get("freeCashFlowPerShareTTM")
        if fcf_per_share is not None:
            info["freeCashflow"] = fcf_per_share  # positive = good, matches scoring logic

    # Call 2: key-metrics-ttm (ROE, absolute FCF)
    metrics = _get("key-metrics-ttm", {"symbol": ticker})
    if isinstance(metrics, list) and metrics:
        m = metrics[0]
        info["returnOnEquity"] = m.get("returnOnEquityTTM")
        # Use absolute FCF if available (better than per-share)
        fcf_equity = m.get("freeCashFlowToEquityTTM")
        if fcf_equity is not None:
            info["freeCashflow"] = fcf_equity

    # Strip None values
    info = {k: v for k, v in info.items() if v is not None}

    # Cache result
    if info:
        _set_cached(ticker, info)

    return info
