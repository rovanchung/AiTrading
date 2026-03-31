"""News sentiment scoring using keyword analysis."""

import logging

from core.data_provider import yf_ticker_news

logger = logging.getLogger("aitrading.analyzer.sentiment")

POSITIVE_KEYWORDS = {
    "upgrade", "beat", "beats", "growth", "profit", "strong", "surge",
    "bullish", "buy", "outperform", "positive", "record", "high",
    "expand", "boost", "rally", "gain", "revenue", "exceed", "optimistic",
    "breakthrough", "innovation", "partnership", "acquisition",
}

NEGATIVE_KEYWORDS = {
    "downgrade", "miss", "misses", "loss", "sell", "weak", "decline",
    "bearish", "underperform", "negative", "low", "cut", "drop",
    "lawsuit", "fraud", "investigation", "recall", "bankruptcy",
    "layoff", "warning", "debt", "default", "crash", "plunge",
}


def compute_sentiment_score(ticker: str) -> tuple[float, dict]:
    """
    Score sentiment 0-100 based on recent news headlines.

    Baseline is 50 (neutral). Each positive keyword adds points,
    each negative keyword subtracts. Clamped to 0-100.

    Returns (score, details_dict).
    """
    details = {"headlines_analyzed": 0, "positive_hits": 0, "negative_hits": 0}

    news = yf_ticker_news(ticker)
    if not news:
        return 50.0, {"note": "no news available"}

    score = 50.0  # Neutral baseline
    headlines = []

    for article in news[:20]:  # Analyze up to 20 recent articles
        title = article.get("title", "") or article.get("content", {}).get("title", "")
        if not title:
            continue
        headlines.append(title)
        words = set(title.lower().split())

        pos_matches = words & POSITIVE_KEYWORDS
        neg_matches = words & NEGATIVE_KEYWORDS

        score += len(pos_matches) * 3
        score -= len(neg_matches) * 3

        details["positive_hits"] += len(pos_matches)
        details["negative_hits"] += len(neg_matches)

    details["headlines_analyzed"] = len(headlines)
    score = max(0.0, min(100.0, score))
    details["total"] = round(score, 2)
    return score, details
