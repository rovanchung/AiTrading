"""Main analyzer — orchestrates all sub-analyzers to score stocks."""

import logging

import pandas as pd

from core.config import Config
from core.database import Database
from core.models import ScoreResult
from analyzer.technical import compute_technical_score
from analyzer.fundamental import compute_fundamental_score
from analyzer.momentum import compute_momentum_score
from analyzer.sentiment import compute_sentiment_score
from analyzer.scoring import compute_composite_score

logger = logging.getLogger("aitrading.analyzer")


def _sanitize(obj):
    """Convert numpy types to native Python for JSON serialization."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


class StockAnalyzer:
    """Orchestrates multi-dimensional stock analysis."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.weights = {
            "technical": config.scoring.get("technical_weight", 0.35),
            "fundamental": config.scoring.get("fundamental_weight", 0.25),
            "momentum": config.scoring.get("momentum_weight", 0.25),
            "sentiment": config.scoring.get("sentiment_weight", 0.15),
        }

    def analyze(
        self,
        ticker: str,
        df: pd.DataFrame,
        spy_df: pd.DataFrame = None,
    ) -> ScoreResult:
        """Run all sub-analyzers and return composite score."""
        all_details = {}

        # Technical analysis
        tech_score, tech_details = compute_technical_score(df)
        all_details["technical"] = tech_details

        # Fundamental analysis (DB-backed, price ratios computed at runtime)
        current_price = float(df["Close"].iloc[-1]) if not df.empty else 0.0
        fund_score, fund_details = compute_fundamental_score(
            ticker, self.db, current_price,
            staleness_days=self.config.get("fundamentals.staleness_days", 80.0),
        )
        all_details["fundamental"] = fund_details

        # Momentum analysis
        mom_score, mom_details = compute_momentum_score(df, spy_df)
        all_details["momentum"] = mom_details

        # Sentiment analysis
        sent_score, sent_details = compute_sentiment_score(ticker)
        all_details["sentiment"] = sent_details

        # Sanitize details for JSON serialization (numpy bools/floats)
        all_details = _sanitize(all_details)

        # Composite
        result = compute_composite_score(
            ticker=ticker,
            technical=tech_score,
            fundamental=fund_score,
            momentum=mom_score,
            sentiment=sent_score,
            weights=self.weights,
            details=all_details,
        )

        # Persist to database
        self.db.save_score(result)

        return result

    def analyze_batch(
        self,
        tickers: list[str],
        data: dict[str, pd.DataFrame],
        spy_df: pd.DataFrame = None,
    ) -> list[ScoreResult]:
        """Analyze multiple stocks and return sorted by composite score."""
        results = []
        for ticker in tickers:
            if ticker not in data:
                continue
            try:
                result = self.analyze(ticker, data[ticker], spy_df)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to analyze {ticker}: {e}")

        results.sort(key=lambda r: r.composite, reverse=True)
        logger.info(
            f"Analyzed {len(results)} stocks. "
            f"Top 5: {[(r.ticker, r.composite) for r in results[:5]]}"
        )
        return results
