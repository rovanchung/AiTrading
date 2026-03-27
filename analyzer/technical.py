"""Technical analysis scoring using pandas-ta indicators."""

import logging

import pandas as pd
import pandas_ta as ta

logger = logging.getLogger("aitrading.analyzer.technical")


def compute_technical_score(df: pd.DataFrame) -> tuple[float, dict]:
    """
    Score a stock 0-100 based on technical indicators.

    Breakdown:
      Trend (40): SMA crossovers + ADX
      Momentum (30): RSI + MACD
      Volume (20): Volume vs average + OBV trend
      Volatility (10): Bollinger width + ATR

    Returns (score, details_dict).
    """
    score = 0.0
    details = {}

    if df.empty or len(df) < 50:
        return 0.0, {"error": "insufficient data"}

    close = df["Close"]
    volume = df["Volume"]

    # --- Trend (40 points) ---
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()

    sma20_above_50 = sma20.iloc[-1] > sma50.iloc[-1]
    details["sma20_above_50"] = sma20_above_50
    if sma20_above_50:
        score += 15

    # SMA50 above SMA200 (golden cross territory)
    if len(df) >= 200:
        sma200 = close.rolling(200).mean()
        sma50_above_200 = sma50.iloc[-1] > sma200.iloc[-1]
        details["sma50_above_200"] = sma50_above_200
        if sma50_above_200:
            score += 15
    else:
        # With less data, award partial points for strong short-term trend
        if sma20_above_50 and close.iloc[-1] > sma20.iloc[-1]:
            score += 10
            details["short_term_uptrend"] = True

    # ADX for trend strength
    adx_df = df.ta.adx(length=14)
    if adx_df is not None and not adx_df.empty:
        adx_val = adx_df.iloc[-1, 0]  # ADX_14
        details["adx"] = round(adx_val, 2)
        if adx_val > 25:
            score += 10

    # --- Momentum (30 points) ---
    rsi = df.ta.rsi(length=14)
    if rsi is not None and not rsi.empty:
        rsi_val = rsi.iloc[-1]
        details["rsi"] = round(rsi_val, 2)
        if 40 <= rsi_val <= 70:
            score += 15
        elif 30 <= rsi_val < 40:
            score += 8  # Approaching oversold, could bounce

    macd_df = df.ta.macd()
    if macd_df is not None and not macd_df.empty:
        hist = macd_df.iloc[:, 2]  # MACD histogram
        details["macd_hist"] = round(hist.iloc[-1], 4)
        if hist.iloc[-1] > 0 and len(hist) >= 2 and hist.iloc[-1] > hist.iloc[-2]:
            score += 15
        elif hist.iloc[-1] > 0:
            score += 8

    # --- Volume (20 points) ---
    vol_sma = volume.rolling(20).mean()
    if vol_sma.iloc[-1] > 0:
        vol_ratio = volume.iloc[-1] / vol_sma.iloc[-1]
        details["volume_ratio"] = round(vol_ratio, 2)
        if vol_ratio > 1.0:
            score += 10

    obv = df.ta.obv()
    if obv is not None and not obv.empty and len(obv) >= 5:
        obv_rising = obv.iloc[-1] > obv.iloc[-5]
        details["obv_rising"] = obv_rising
        if obv_rising:
            score += 10

    # --- Volatility (10 points) ---
    bbands = df.ta.bbands(length=20, std=2)
    if bbands is not None and not bbands.empty:
        lower = bbands.iloc[-1, 0]  # BBL
        mid = bbands.iloc[-1, 1]    # BBM
        upper = bbands.iloc[-1, 2]  # BBU
        if mid > 0:
            bb_width = (upper - lower) / mid
            details["bb_width"] = round(bb_width, 4)
            if 0.02 < bb_width < 0.15:
                score += 5

    atr = df.ta.atr(length=14)
    if atr is not None and not atr.empty and close.iloc[-1] > 0:
        atr_pct = atr.iloc[-1] / close.iloc[-1]
        details["atr_pct"] = round(atr_pct, 4)
        if 0.01 < atr_pct < 0.05:
            score += 5

    details["total"] = round(score, 2)
    return min(score, 100.0), details
