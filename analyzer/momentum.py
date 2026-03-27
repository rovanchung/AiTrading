"""Price and volume momentum scoring."""

import logging

import pandas as pd

logger = logging.getLogger("aitrading.analyzer.momentum")


def compute_momentum_score(
    df: pd.DataFrame,
    spy_df: pd.DataFrame = None,
) -> tuple[float, dict]:
    """
    Score a stock 0-100 based on price momentum.

    Breakdown:
      Price performance (50): 1-month and 3-month returns
      Acceleration (30): recent momentum acceleration, consecutive up days
      Relative strength (20): performance vs SPY

    Returns (score, details_dict).
    """
    score = 0.0
    details = {}

    if df.empty or len(df) < 20:
        return 0.0, {"error": "insufficient data"}

    close = df["Close"]

    # --- Price Performance (50 points) ---
    # 1-month return (~20 trading days)
    ret_1m = (close.iloc[-1] / close.iloc[-20] - 1) if len(df) >= 20 else 0
    details["return_1m_pct"] = round(ret_1m * 100, 2)

    if ret_1m > 0.10:
        score += 25
    elif ret_1m > 0.05:
        score += 20
    elif ret_1m > 0.02:
        score += 15
    elif ret_1m > 0:
        score += 8

    # 3-month return (~60 trading days)
    if len(df) >= 60:
        ret_3m = close.iloc[-1] / close.iloc[-60] - 1
        details["return_3m_pct"] = round(ret_3m * 100, 2)

        if ret_3m > 0.20:
            score += 25
        elif ret_3m > 0.10:
            score += 20
        elif ret_3m > 0.05:
            score += 15
        elif ret_3m > 0:
            score += 8
    else:
        # Scale 1m return for partial credit
        score += min(ret_1m * 100, 15)

    # --- Acceleration (30 points) ---
    # Is 1-month return accelerating vs 3-month average?
    if len(df) >= 60:
        avg_monthly_3m = ret_3m / 3
        if ret_1m > avg_monthly_3m * 1.2:  # 20% acceleration
            score += 15
            details["accelerating"] = True
        else:
            details["accelerating"] = False

    # Consecutive up days in last 10
    recent = close.tail(10)
    up_days = sum(1 for i in range(1, len(recent)) if recent.iloc[i] > recent.iloc[i - 1])
    details["up_days_last_10"] = up_days
    if up_days >= 7:
        score += 15
    elif up_days >= 6:
        score += 10
    elif up_days >= 5:
        score += 5

    # --- Relative Strength vs SPY (20 points) ---
    if spy_df is not None and not spy_df.empty and len(spy_df) >= 20:
        spy_close = spy_df["Close"]

        spy_ret_1m = spy_close.iloc[-1] / spy_close.iloc[-20] - 1
        details["spy_return_1m_pct"] = round(spy_ret_1m * 100, 2)

        if ret_1m > spy_ret_1m:
            score += 10
            details["outperforming_spy_1m"] = True

        if len(spy_df) >= 60 and len(df) >= 60:
            spy_ret_3m = spy_close.iloc[-1] / spy_close.iloc[-60] - 1
            ret_3m = close.iloc[-1] / close.iloc[-60] - 1
            if ret_3m > spy_ret_3m:
                score += 10
                details["outperforming_spy_3m"] = True
    else:
        # No SPY data, give half credit
        score += 10

    details["total"] = round(score, 2)
    return min(score, 100.0), details
