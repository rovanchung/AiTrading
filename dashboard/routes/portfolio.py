"""Portfolio page."""

from flask import Blueprint, render_template
from dashboard.db import query, query_one

portfolio_bp = Blueprint("portfolio", __name__)


@portfolio_bp.route("/")
def index():
    snapshot = query_one(
        "SELECT * FROM portfolio_snapshots ORDER BY snapshot_time DESC LIMIT 1"
    )
    open_positions = query(
        "SELECT sector, SUM(qty * entry_price) as allocation "
        "FROM positions WHERE status='open' AND sector != '' "
        "GROUP BY sector ORDER BY allocation DESC"
    )
    closed = query(
        "SELECT pnl, exit_reason FROM positions WHERE status='closed' AND pnl IS NOT NULL"
    )
    total_pnl = sum(r["pnl"] for r in closed)
    wins = sum(1 for r in closed if r["pnl"] > 0)
    losses = sum(1 for r in closed if r["pnl"] <= 0)

    return render_template(
        "portfolio.html",
        snapshot=snapshot,
        sector_data=open_positions,
        total_pnl=total_pnl,
        wins=wins,
        losses=losses,
        total_trades=len(closed),
    )
