"""Rankings page."""

from flask import Blueprint, render_template
from dashboard.db import query

rankings_bp = Blueprint("rankings", __name__)


@rankings_bp.route("/")
def index():
    rankings = query("""
        SELECT s.ticker, s.scored_at, s.technical_score, s.fundamental_score,
               s.momentum_score, s.sentiment_score, s.composite_score,
               u.name, u.sector, u.market_cap
        FROM scores s
        INNER JOIN (
            SELECT ticker, MAX(scored_at) as max_scored
            FROM scores GROUP BY ticker
        ) latest ON s.ticker = latest.ticker AND s.scored_at = latest.max_scored
        LEFT JOIN universe u ON s.ticker = u.ticker
        ORDER BY s.composite_score DESC
    """)
    return render_template("rankings.html", rankings=rankings)
