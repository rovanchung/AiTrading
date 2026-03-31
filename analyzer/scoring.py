"""Composite score aggregation."""

import logging

from core.models import ScoreResult

logger = logging.getLogger("aitrading.analyzer.scoring")

DEFAULT_WEIGHTS = {
    "technical": 0.35,
    "fundamental": 0.25,
    "momentum": 0.25,
    "sentiment": 0.15,
}


def compute_composite_score(
    ticker: str,
    technical: float,
    fundamental: float,
    momentum: float,
    sentiment: float,
    weights: dict = None,
    details: dict = None,
) -> ScoreResult:
    """Compute weighted composite score and return a ScoreResult."""
    w = weights or DEFAULT_WEIGHTS

    composite = (
        technical * w["technical"]
        + fundamental * w["fundamental"]
        + momentum * w["momentum"]
        + sentiment * w["sentiment"]
    )

    result = ScoreResult(
        ticker=ticker,
        technical=round(technical, 2),
        fundamental=round(fundamental, 2),
        momentum=round(momentum, 2),
        sentiment=round(sentiment, 2),
        composite=round(composite, 2),
        details=details or {},
    )

    logger.debug(
        f"{ticker}: T={result.technical} F={result.fundamental} "
        f"M={result.momentum} S={result.sentiment} => C={result.composite}"
    )
    return result
