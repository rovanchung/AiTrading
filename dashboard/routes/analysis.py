"""Per-stock analysis page."""

import json
from flask import Blueprint, render_template
from dashboard.db import query, query_one

analysis_bp = Blueprint("analysis", __name__)


@analysis_bp.route("/<ticker>")
def detail(ticker):
    ticker = ticker.upper()

    # Universe info
    stock = query_one("SELECT * FROM universe WHERE ticker = ?", (ticker,))

    # Latest score
    score = query_one(
        "SELECT * FROM scores WHERE ticker = ? ORDER BY scored_at DESC LIMIT 1",
        (ticker,),
    )
    if score and score.get("details"):
        try:
            score["details"] = json.loads(score["details"])
        except (json.JSONDecodeError, TypeError):
            score["details"] = {}

    # Fundamentals
    fundamentals = query_one(
        "SELECT * FROM fundamentals WHERE ticker = ?", (ticker,)
    )

    # Position history
    positions = query(
        "SELECT * FROM positions WHERE ticker = ? ORDER BY entry_time DESC",
        (ticker,),
    )

    # Score history. Scores are re-recorded every rebalance cycle (~1/min), so a
    # plain "latest N rows" spans only the last N minutes and renders as a flat
    # line. Collapse consecutive identical composite scores to their change-points
    # so the chart shows the real evolution over time, then keep the most recent.
    score_history = query(
        "SELECT scored_at, composite_score FROM ("
        "  SELECT scored_at, composite_score, "
        "         LAG(composite_score) OVER (ORDER BY scored_at) AS prev "
        "  FROM scores WHERE ticker = ?"
        ") WHERE prev IS NULL OR composite_score <> prev "
        "ORDER BY scored_at DESC LIMIT 40",
        (ticker,),
    )
    score_history.reverse()

    return render_template(
        "analysis.html",
        ticker=ticker,
        stock=stock,
        score=score,
        fundamentals=fundamentals,
        positions=positions,
        score_history=score_history,
    )
