"""Fundamental analysis scoring — DB-backed with runtime price ratios."""

import logging

from core.database import Database
from core.data_provider import fetch_fundamentals

logger = logging.getLogger("aitrading.analyzer.fundamental")


def compute_fundamental_score(
    ticker: str,
    db: Database,
    current_price: float,
    staleness_days: float = 80.0,
) -> tuple[float, dict]:
    """
    Score a stock 0-100 based on fundamental ratios.

    Reads from the fundamentals DB table, fetching fresh data only when
    stale (>staleness_days old). Price-dependent ratios (P/E, P/B, PEG)
    are computed at runtime using current_price.

    Breakdown:
      Valuation (35): P/E, PEG, P/B  — computed from stored EPS/BVPS + live price
      Profitability (35): ROE, profit margin, revenue growth
      Financial health (30): current ratio, debt/equity, free cash flow

    Returns (score, details_dict).
    """
    fund = _get_or_fetch(ticker, db, staleness_days)
    if fund is None:
        return 50.0, {"error": "all providers failed, no cached data"}

    score = 0.0
    details = {}

    # --- Valuation (35 points) — runtime price ratios ---
    pe = None
    eps = fund.get("eps_ttm")
    if eps and eps > 0 and current_price > 0:
        pe = current_price / eps
        details["pe"] = round(pe, 2)
        if pe < 15:
            score += 15
        elif pe < 25:
            score += 10
        elif pe < 35:
            score += 5

    bvps = fund.get("book_value_per_share_quarterly") or fund.get("book_value_per_share_annual")
    if bvps and bvps > 0 and current_price > 0:
        pb = current_price / bvps
        details["pb"] = round(pb, 2)
        if 0 < pb < 3:
            score += 10
        elif 3 <= pb < 5:
            score += 5

    eg = fund.get("earnings_growth_ttm")
    if pe is not None and eg and eg > 0:
        peg = pe / (eg * 100)  # eg is decimal (0.15), PEG uses percentage growth (15)
        details["peg"] = round(peg, 2)
        if 0 < peg < 1:
            score += 10
        elif 1 <= peg < 2:
            score += 5

    # --- Profitability (35 points) ---
    roe = fund.get("roe_ttm") or fund.get("roe_annual")
    if roe is not None:
        roe_pct = roe * 100
        details["roe_pct"] = round(roe_pct, 2)
        if roe_pct > 15:
            score += 15
        elif roe_pct > 10:
            score += 10
        elif roe_pct > 5:
            score += 5

    margin = fund.get("net_margin_ttm")
    if margin is not None:
        margin_pct = margin * 100
        details["profit_margin_pct"] = round(margin_pct, 2)
        if margin_pct > 10:
            score += 10
        elif margin_pct > 5:
            score += 5

    rev_growth = fund.get("revenue_growth_ttm_yoy") or fund.get("revenue_growth_3y")
    if rev_growth is not None:
        rev_growth_pct = rev_growth * 100
        details["revenue_growth_pct"] = round(rev_growth_pct, 2)
        if rev_growth_pct > 10:
            score += 10
        elif rev_growth_pct > 0:
            score += 5

    # --- Financial Health (30 points) ---
    current_ratio = fund.get("current_ratio_quarterly") or fund.get("current_ratio_annual")
    if current_ratio is not None:
        details["current_ratio"] = round(current_ratio, 2)
        if current_ratio > 1.5:
            score += 10
        elif current_ratio > 1.0:
            score += 5

    de = fund.get("debt_to_equity_annual")
    if de is not None:
        de_pct = de * 100  # ratio -> percentage for scoring thresholds
        details["debt_to_equity"] = round(de_pct, 2)
        if de_pct < 50:
            score += 10
        elif de_pct < 100:
            score += 5

    fcf = fund.get("free_cash_flow_ttm")
    if fcf is not None:
        details["free_cashflow"] = fcf
        if fcf > 0:
            score += 10

    details["total"] = round(score, 2)
    details["provider"] = fund.get("provider", "unknown")
    return min(score, 100.0), details


def _get_or_fetch(ticker: str, db: Database, staleness_days: float) -> dict | None:
    """Return fundamental data from DB, fetching fresh if stale or missing."""
    age = db.get_fundamentals_age_days(ticker)
    cached = db.get_fundamentals(ticker)

    if cached and age is not None and age < staleness_days:
        logger.debug(
            f"Using cached fundamentals for {ticker} "
            f"({age:.1f} days old, provider={cached.get('provider', '?')})"
        )
        return cached

    if cached and age is not None:
        logger.info(
            f"Fundamentals for {ticker} are {age:.0f} days old "
            f"(threshold={staleness_days}), refreshing"
        )

    result = fetch_fundamentals(ticker)
    if result is not None:
        data, provider, raw_json = result
        db.upsert_fundamentals(ticker, data, provider, raw_json)
        # Return the freshly stored data
        return db.get_fundamentals(ticker)

    # All providers failed — use stale data if available
    if cached:
        logger.warning(
            f"All providers failed for {ticker}, using stale data "
            f"({age:.0f} days old)"
        )
        return cached

    return None
