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

    # Score history (last 20)
    score_history = query(
        "SELECT scored_at, composite_score FROM scores WHERE ticker = ? "
        "ORDER BY scored_at DESC LIMIT 20",
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
