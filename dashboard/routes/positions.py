"""Positions page."""

from flask import Blueprint, render_template
from dashboard.db import query

positions_bp = Blueprint("positions", __name__)


@positions_bp.route("/")
def index():
    open_positions = query(
        "SELECT * FROM positions WHERE status='open' ORDER BY entry_time DESC"
    )
    closed_positions = query(
        "SELECT * FROM positions WHERE status='closed' ORDER BY exit_time DESC"
    )
    return render_template(
        "positions.html",
        open_positions=open_positions,
        closed_positions=closed_positions,
    )
