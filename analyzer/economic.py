"""Economic/macro analysis — portfolio-level overlay.

Fetches macro indicators, classifies market regime, and adjusts
portfolio parameters (buy threshold, max positions, cash reserve,
sector preferences) based on economic conditions.
"""

import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from core.yf_helpers import yf_download

logger = logging.getLogger("aitrading.analyzer.economic")

# Economic cycle → favored/disfavored sectors
# Based on sector rotation model (Sam Stovall's framework)
CYCLE_SECTOR_MAP = {
    "early_recovery": {
        "favored": {"Consumer Discretionary", "Financials", "Real Estate", "Industrials"},
        "neutral": {"Information Technology", "Communication Services", "Materials"},
        "disfavored": {"Utilities", "Consumer Staples", "Health Care", "Energy"},
    },
    "expansion": {
        "favored": {"Information Technology", "Communication Services", "Industrials", "Materials"},
        "neutral": {"Consumer Discretionary", "Financials", "Health Care"},
        "disfavored": {"Utilities", "Consumer Staples", "Real Estate", "Energy"},
    },
    "late_cycle": {
        "favored": {"Energy", "Materials", "Industrials", "Health Care"},
        "neutral": {"Consumer Staples", "Utilities", "Financials"},
        "disfavored": {"Information Technology", "Consumer Discretionary", "Communication Services", "Real Estate"},
    },
    "recession": {
        "favored": {"Utilities", "Consumer Staples", "Health Care"},
        "neutral": {"Communication Services", "Real Estate", "Financials"},
        "disfavored": {"Consumer Discretionary", "Information Technology", "Industrials", "Materials", "Energy"},
    },
}

# Sector limit adjustments by preference
SECTOR_LIMIT_FAVORED = 0.40      # Allow up to 40% in favored sectors
SECTOR_LIMIT_NEUTRAL = 0.30      # Default 30%
SECTOR_LIMIT_DISFAVORED = 0.15   # Restrict disfavored sectors to 15%


class MacroAnalyzer:
    """Analyzes macroeconomic conditions and adjusts portfolio parameters."""

    def __init__(self):
        self._cache = {}
        self._cache_time = None
        self._cache_ttl_hours = 4  # Refresh macro data every 4 hours

    def get_macro_assessment(self) -> dict:
        """
        Compute macro risk score and regime classification.

        Returns dict with:
          - macro_score: 0-100 (higher = more risk-on)
          - regime: 'risk_on', 'neutral', 'risk_off'
          - cycle_phase: 'early_recovery', 'expansion', 'late_cycle', 'recession'
          - indicators: dict of individual indicator values
          - adjustments: dict of parameter adjustments to apply
        """
        if self._is_cache_valid():
            return self._cache

        indicators = {}

        # 1. VIX — fear gauge
        vix = self._get_vix()
        indicators["vix"] = vix
        vix_score = self._score_vix(vix)

        # 2. Yield curve (10Y - 2Y treasury spread)
        yield_spread = self._get_yield_spread()
        indicators["yield_spread"] = yield_spread
        yield_score = self._score_yield_spread(yield_spread)

        # 3. Market breadth (% of S&P 500 above 200-day SMA)
        breadth = self._get_market_breadth()
        indicators["market_breadth_pct"] = breadth
        breadth_score = self._score_breadth(breadth)

        # 4. SPY trend (price vs 200-day SMA)
        spy_trend = self._get_spy_trend()
        indicators["spy_above_200sma"] = spy_trend["above_200sma"]
        indicators["spy_distance_pct"] = spy_trend["distance_pct"]
        trend_score = self._score_spy_trend(spy_trend)

        # 5. Rate environment (TNX direction over 3 months)
        rate_trend = self._get_rate_trend()
        indicators["rates_rising"] = rate_trend["rising"]
        indicators["rate_change_pct"] = rate_trend["change_pct"]
        rate_score = self._score_rate_trend(rate_trend)

        # Weighted macro score (0-100, higher = more bullish)
        macro_score = (
            vix_score * 0.25
            + yield_score * 0.20
            + breadth_score * 0.25
            + trend_score * 0.20
            + rate_score * 0.10
        )

        # Classify regime
        if macro_score >= 65:
            regime = "risk_on"
        elif macro_score >= 40:
            regime = "neutral"
        else:
            regime = "risk_off"

        # Classify economic cycle phase
        cycle_phase = self._classify_cycle(indicators, macro_score)

        # Compute parameter adjustments
        adjustments = self._compute_adjustments(regime, cycle_phase)

        result = {
            "macro_score": round(macro_score, 1),
            "regime": regime,
            "cycle_phase": cycle_phase,
            "indicators": indicators,
            "adjustments": adjustments,
            "assessed_at": datetime.now().isoformat(),
        }

        self._cache = result
        self._cache_time = datetime.now()

        logger.info(
            f"Macro assessment: score={macro_score:.1f}, regime={regime}, "
            f"cycle={cycle_phase}, VIX={vix:.1f}, breadth={breadth:.0f}%"
        )
        return result

    # --- Data Fetchers ---

    def _get_vix(self) -> float:
        """Fetch current VIX (CBOE Volatility Index)."""
        df = yf_download("^VIX", period="5d")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        if not df.empty:
            return float(df["Close"].iloc[-1])
        return 20.0  # Historical average as fallback

    def _get_yield_spread(self) -> float:
        """Fetch 10Y-2Y treasury spread (yield curve)."""
        tnx = yf_download("^TNX", period="5d")  # 10Y yield
        twy = yf_download("^IRX", period="5d")  # 13-week T-bill (proxy for short end)
        for df in [tnx, twy]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
        if not tnx.empty and not twy.empty:
            y10 = float(tnx["Close"].iloc[-1])
            y_short = float(twy["Close"].iloc[-1])
            return y10 - y_short
        return 0.5  # Mild positive slope as fallback

    def _get_market_breadth(self) -> float:
        """Estimate market breadth: % of SPY components in uptrend.

        Uses a sample of sector ETFs as a proxy for full breadth calculation.
        """
        sector_etfs = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB"]
        df = yf_download(sector_etfs, period="1y", group_by="ticker", timeout=20)
        if df.empty:
            return 60.0  # Neutral fallback
        above_200 = 0
        total = 0
        for etf in sector_etfs:
            try:
                if etf in df.columns.get_level_values(0):
                    etf_df = df[etf].dropna()
                    if len(etf_df) >= 200:
                        sma200 = etf_df["Close"].rolling(200).mean().iloc[-1]
                        if etf_df["Close"].iloc[-1] > sma200:
                            above_200 += 1
                        total += 1
            except (KeyError, TypeError):
                continue
        if total > 0:
            return (above_200 / total) * 100
        return 60.0  # Neutral fallback

    def _get_spy_trend(self) -> dict:
        """Check SPY's position relative to its 200-day SMA."""
        df = yf_download("SPY", period="1y")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        if not df.empty and len(df) >= 200:
            sma200 = df["Close"].rolling(200).mean().iloc[-1]
            current = df["Close"].iloc[-1]
            distance = (current - sma200) / sma200 * 100
            return {"above_200sma": current > sma200, "distance_pct": round(distance, 2)}
        return {"above_200sma": True, "distance_pct": 0.0}

    def _get_rate_trend(self) -> dict:
        """Check if interest rates are rising or falling over 3 months."""
        df = yf_download("^TNX", period="3mo")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        if not df.empty and len(df) >= 20:
            start = df["Close"].iloc[0]
            end = df["Close"].iloc[-1]
            change = end - start
            return {"rising": change > 0.1, "change_pct": round(change, 2)}
        return {"rising": False, "change_pct": 0.0}

    # --- Scorers (each returns 0-100) ---

    def _score_vix(self, vix: float) -> float:
        """Low VIX = bullish. VIX < 15 = complacent (80pts), 15-20 = normal (60),
        20-30 = elevated (30), > 30 = fear (10)."""
        if vix < 15:
            return 80.0
        elif vix < 20:
            return 60.0
        elif vix < 25:
            return 45.0
        elif vix < 30:
            return 30.0
        else:
            return 10.0

    def _score_yield_spread(self, spread: float) -> float:
        """Positive spread = healthy economy. Inverted curve = recession signal.
        > 1.0 = strong (80), 0.5-1.0 = normal (60), 0-0.5 = flat (40),
        < 0 = inverted (15)."""
        if spread > 1.0:
            return 80.0
        elif spread > 0.5:
            return 60.0
        elif spread > 0:
            return 40.0
        else:
            return 15.0

    def _score_breadth(self, breadth: float) -> float:
        """Higher breadth = healthier market. > 70% = strong (85),
        50-70% = OK (55), 30-50% = weak (30), < 30% = bear (10)."""
        if breadth > 70:
            return 85.0
        elif breadth > 50:
            return 55.0
        elif breadth > 30:
            return 30.0
        else:
            return 10.0

    def _score_spy_trend(self, trend: dict) -> float:
        """SPY above 200-SMA = bullish. Distance matters too."""
        if not trend["above_200sma"]:
            # Below 200-SMA: bear territory
            return max(10, 40 + trend["distance_pct"])  # distance is negative
        else:
            # Above: scale by distance
            return min(90, 50 + trend["distance_pct"] * 2)

    def _score_rate_trend(self, trend: dict) -> float:
        """Rising rates = headwind for stocks (but not always bad).
        Rapidly rising = negative. Stable/falling = positive."""
        change = trend["change_pct"]
        if change > 0.5:
            return 20.0  # Rapidly rising — headwind
        elif change > 0.1:
            return 40.0  # Mildly rising
        elif change > -0.1:
            return 60.0  # Stable
        else:
            return 75.0  # Falling — tailwind

    # --- Cycle Classification ---

    def _classify_cycle(self, indicators: dict, macro_score: float) -> str:
        """Classify economic cycle phase from indicators."""
        breadth = indicators.get("market_breadth_pct", 60)
        yield_spread = indicators.get("yield_spread", 0.5)
        spy_above = indicators.get("spy_above_200sma", True)
        rates_rising = indicators.get("rates_rising", False)

        # Recession: inverted yield curve + SPY below 200-SMA + low breadth
        if yield_spread < 0 and not spy_above and breadth < 40:
            return "recession"

        # Early recovery: positive yield curve + SPY recovering + improving breadth
        if yield_spread > 0.5 and not rates_rising and macro_score > 50 and breadth > 40:
            if indicators.get("spy_distance_pct", 0) < 5:  # Close to 200-SMA
                return "early_recovery"

        # Late cycle: rising rates + high breadth starting to narrow + elevated VIX
        if rates_rising and indicators.get("vix", 20) > 20 and breadth < 60:
            return "late_cycle"

        # Expansion: default if things look generally healthy
        if spy_above and breadth > 50:
            return "expansion"

        # Ambiguous — default to late_cycle (more conservative)
        return "late_cycle"

    # --- Parameter Adjustments ---

    def _compute_adjustments(self, regime: str, cycle_phase: str) -> dict:
        """Compute trading parameter adjustments based on regime and cycle."""
        adjustments = {}

        # Buy threshold adjustment
        if regime == "risk_on":
            adjustments["buy_threshold"] = -5     # 65 → 60
            adjustments["max_positions"] = 0       # Keep at 10
            adjustments["cash_reserve_add"] = -0.05  # 20% → 15%
        elif regime == "neutral":
            adjustments["buy_threshold"] = 0
            adjustments["max_positions"] = -2      # 10 → 8
            adjustments["cash_reserve_add"] = 0.0
        else:  # risk_off
            adjustments["buy_threshold"] = 10      # 65 → 75
            adjustments["max_positions"] = -5      # 10 → 5
            adjustments["cash_reserve_add"] = 0.15  # 20% → 35%

        # Sector preferences based on cycle
        sector_map = CYCLE_SECTOR_MAP.get(cycle_phase, CYCLE_SECTOR_MAP["expansion"])
        sector_limits = {}
        for sector in sector_map.get("favored", set()):
            sector_limits[sector] = SECTOR_LIMIT_FAVORED
        for sector in sector_map.get("neutral", set()):
            sector_limits[sector] = SECTOR_LIMIT_NEUTRAL
        for sector in sector_map.get("disfavored", set()):
            sector_limits[sector] = SECTOR_LIMIT_DISFAVORED
        adjustments["sector_limits"] = sector_limits

        return adjustments

    def _is_cache_valid(self) -> bool:
        if not self._cache or not self._cache_time:
            return False
        age_hours = (datetime.now() - self._cache_time).total_seconds() / 3600
        return age_hours < self._cache_ttl_hours
