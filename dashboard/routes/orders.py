"""Orders page."""

from datetime import datetime, timedelta

from flask import Blueprint, render_template, request

from dashboard.db import query

orders_bp = Blueprint("orders", __name__)


RANGE_OPTIONS = [
    ("1d", "Last 24h", 1),
    ("7d", "Last 7 days", 7),
    ("30d", "Last 30 days", 30),
    ("90d", "Last 90 days", 90),
    ("all", "All time", None),
]


def _range_cutoff(range_key: str):
    for key, _label, days in RANGE_OPTIONS:
        if key == range_key:
            if days is None:
                return None
            return (datetime.now() - timedelta(days=days)).isoformat(sep=" ")
    return None


@orders_bp.route("/")
def index():
    range_key = request.args.get("range", "7d")
    if range_key not in {k for k, _l, _d in RANGE_OPTIONS}:
        range_key = "7d"
    cutoff = _range_cutoff(range_key)

    if cutoff:
        orders = query(
            "SELECT * FROM orders WHERE submitted_at >= ? "
            "ORDER BY submitted_at DESC",
            (cutoff,),
        )
    else:
        orders = query("SELECT * FROM orders ORDER BY submitted_at DESC")

    return render_template(
        "orders.html",
        orders=orders,
        range_options=RANGE_OPTIONS,
        active_range=range_key,
    )
