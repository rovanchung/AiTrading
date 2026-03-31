"""Dashboard home page."""

from flask import Blueprint, render_template
from dashboard.db import query, query_one

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    snapshot = query_one(
        "SELECT * FROM portfolio_snapshots ORDER BY snapshot_time DESC LIMIT 1"
    )
    open_positions = query(
        "SELECT * FROM positions WHERE status='open' ORDER BY entry_time DESC"
    )
    closed = query(
        "SELECT pnl FROM positions WHERE status='closed' AND pnl IS NOT NULL"
    )
    recent_orders = query(
        "SELECT * FROM orders ORDER BY submitted_at DESC LIMIT 10"
    )
    top_scores = query("""
        SELECT s.ticker, s.composite_score, s.technical_score, s.fundamental_score,
               s.momentum_score, s.sentiment_score, u.name, u.sector
        FROM scores s
        INNER JOIN (
            SELECT ticker, MAX(scored_at) as max_scored
            FROM scores GROUP BY ticker
        ) latest ON s.ticker = latest.ticker AND s.scored_at = latest.max_scored
        LEFT JOIN universe u ON s.ticker = u.ticker
        ORDER BY s.composite_score DESC LIMIT 10
    """)

    total_pnl = sum(r["pnl"] for r in closed)
    wins = sum(1 for r in closed if r["pnl"] > 0)
    total_trades = len(closed)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    return render_template(
        "dashboard.html",
        snapshot=snapshot,
        open_positions=open_positions,
        total_pnl=total_pnl,
        win_rate=win_rate,
        total_trades=total_trades,
        recent_orders=recent_orders,
        top_scores=top_scores,
    )
