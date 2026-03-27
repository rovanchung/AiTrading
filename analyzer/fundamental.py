"""Fundamental analysis scoring using yfinance data."""

import logging

import yfinance as yf

logger = logging.getLogger("aitrading.analyzer.fundamental")


def compute_fundamental_score(ticker: str) -> tuple[float, dict]:
    """
    Score a stock 0-100 based on fundamental ratios.

    Breakdown:
      Valuation (35): P/E, PEG, P/B
      Profitability (35): ROE, profit margin, revenue growth
      Financial health (30): current ratio, debt/equity, free cash flow

    Returns (score, details_dict).
    """
    score = 0.0
    details = {}

    try:
        info = yf.Ticker(ticker).info
    except Exception as e:
        logger.warning(f"Failed to fetch fundamentals for {ticker}: {e}")
        return 50.0, {"error": str(e)}  # Neutral score on failure

    # --- Valuation (35 points) ---
    pe = info.get("trailingPE") or info.get("forwardPE")
    if pe is not None:
        details["pe"] = round(pe, 2)
        if pe < 15:
            score += 15
        elif pe < 25:
            score += 10
        elif pe < 35:
            score += 5

    peg = info.get("pegRatio")
    if peg is not None:
        details["peg"] = round(peg, 2)
        if 0 < peg < 1:
            score += 10
        elif 1 <= peg < 2:
            score += 5

    pb = info.get("priceToBook")
    if pb is not None:
        details["pb"] = round(pb, 2)
        if 0 < pb < 3:
            score += 10
        elif 3 <= pb < 5:
            score += 5

    # --- Profitability (35 points) ---
    roe = info.get("returnOnEquity")
    if roe is not None:
        roe_pct = roe * 100
        details["roe_pct"] = round(roe_pct, 2)
        if roe_pct > 15:
            score += 15
        elif roe_pct > 10:
            score += 10
        elif roe_pct > 5:
            score += 5

    margin = info.get("profitMargins")
    if margin is not None:
        margin_pct = margin * 100
        details["profit_margin_pct"] = round(margin_pct, 2)
        if margin_pct > 10:
            score += 10
        elif margin_pct > 5:
            score += 5

    rev_growth = info.get("revenueGrowth")
    if rev_growth is not None:
        rev_growth_pct = rev_growth * 100
        details["revenue_growth_pct"] = round(rev_growth_pct, 2)
        if rev_growth_pct > 10:
            score += 10
        elif rev_growth_pct > 0:
            score += 5

    # --- Financial Health (30 points) ---
    current_ratio = info.get("currentRatio")
    if current_ratio is not None:
        details["current_ratio"] = round(current_ratio, 2)
        if current_ratio > 1.5:
            score += 10
        elif current_ratio > 1.0:
            score += 5

    de = info.get("debtToEquity")
    if de is not None:
        details["debt_to_equity"] = round(de, 2)
        if de < 50:  # yfinance reports as percentage
            score += 10
        elif de < 100:
            score += 5

    fcf = info.get("freeCashflow")
    if fcf is not None:
        details["free_cashflow"] = fcf
        if fcf > 0:
            score += 10

    details["total"] = round(score, 2)
    return min(score, 100.0), details
