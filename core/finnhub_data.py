"""Finnhub data provider — primary source for fundamental data.

Free tier: 60 calls/min, no daily cap. Uses the /stock/metric endpoint
with metric=all to get comprehensive fundamental data in a single call.
"""

import json
import logging
import os
import time

import requests

logger = logging.getLogger("aitrading.core.finnhub_data")

_BASE = "https://finnhub.io/api/v1"
_TIMEOUT = 10

# --- Rate limiter (60 calls/min sliding window) ---
_call_timestamps: list[float] = []
_RATE_LIMIT = 55  # stay under 60 to be safe
_RATE_WINDOW = 60  # seconds

# Field mapping: Finnhub metric name -> (DB column, needs_pct_conversion)
# Percentage fields (ROE, margins, growth) come as e.g. 15.2 meaning 15.2%,
# must divide by 100 for decimal storage.
_FIELD_MAP = {
    "epsTTM": ("eps_ttm", False),
    "epsAnnual": ("eps_annual", False),
    "bookValuePerShareQuarterly": ("book_value_per_share_quarterly", False),
    "bookValuePerShareAnnual": ("book_value_per_share_annual", False),
    "epsGrowthTTMYoy": ("earnings_growth_ttm", True),
    "epsGrowth5Y": ("earnings_growth_5y", True),
    "roeTTM": ("roe_ttm", True),
    "roeAnnual": ("roe_annual", True),
    "netMarginTTM": ("net_margin_ttm", True),
    "grossMarginTTM": ("gross_margin_ttm", True),
    "operatingMarginTTM": ("operating_margin_ttm", True),
    "revenueGrowthTTMYoy": ("revenue_growth_ttm_yoy", True),
    "revenueGrowth3Y": ("revenue_growth_3y", True),
    "revenueGrowth5Y": ("revenue_growth_5y", True),
    "currentRatioQuarterly": ("current_ratio_quarterly", False),
    "currentRatioAnnual": ("current_ratio_annual", False),
    "totalDebt/totalEquityAnnual": ("debt_to_equity_annual", False),
    "freeCashFlowTTM": ("free_cash_flow_ttm", False),
    "fcfPerShareTTM": ("fcf_per_share_ttm", False),
}


def _rate_limit_wait():
    """Block until a call slot is available within the 60/min window."""
    now = time.monotonic()
    # Prune timestamps older than window
    _call_timestamps[:] = [t for t in _call_timestamps if now - t < _RATE_WINDOW]
    if len(_call_timestamps) >= _RATE_LIMIT:
        sleep_time = _RATE_WINDOW - (now - _call_timestamps[0]) + 0.1
        logger.debug(f"Finnhub rate limit: sleeping {sleep_time:.1f}s")
        time.sleep(sleep_time)
        # Re-prune after sleeping
        now = time.monotonic()
        _call_timestamps[:] = [t for t in _call_timestamps if now - t < _RATE_WINDOW]
    _call_timestamps.append(time.monotonic())


def _api_key() -> str:
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        raise ValueError("FINNHUB_API_KEY not set in environment")
    return key


def finnhub_fundamentals(ticker: str) -> tuple[dict, str]:
    """Fetch fundamental metrics from Finnhub, return normalized dict.

    Returns (normalized_data, raw_json_string).
    normalized_data keys match the fundamentals DB columns.
    Values are decimals: ROE 0.15 = 15%, margins/growth likewise.
    Debt/equity as ratio (not percentage).

    Raises on network/auth errors so caller can fall back.
    """
    _rate_limit_wait()

    params = {"symbol": ticker, "metric": "all", "token": _api_key()}

    try:
        r = requests.get(f"{_BASE}/stock/metric", params=params, timeout=_TIMEOUT)
    except requests.Timeout:
        logger.warning(f"Finnhub timeout for {ticker}")
        raise
    except requests.ConnectionError:
        logger.warning(f"Finnhub connection error for {ticker}")
        raise

    if r.status_code == 401:
        logger.error(f"Finnhub auth failed (401) for {ticker} — check FINNHUB_API_KEY")
        raise requests.HTTPError("Finnhub 401: invalid API key", response=r)
    if r.status_code == 429:
        logger.warning(f"Finnhub rate limited (429) for {ticker}")
        raise requests.HTTPError("Finnhub 429: rate limited", response=r)
    r.raise_for_status()

    resp = r.json()
    m = resp.get("metric") or {}
    if not m:
        logger.warning(f"Finnhub returned empty metrics for {ticker}")
        return {}, "{}"

    data = {}
    for finnhub_key, (db_col, is_pct) in _FIELD_MAP.items():
        val = m.get(finnhub_key)
        if val is not None:
            if is_pct:
                val = val / 100.0
            data[db_col] = val

    logger.debug(f"Finnhub fetched {len(data)} fields for {ticker}")
    return data, json.dumps(m)
