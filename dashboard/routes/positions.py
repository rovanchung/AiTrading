"""Positions page."""

from datetime import datetime, timedelta

from flask import Blueprint, render_template, request

from dashboard.db import query

positions_bp = Blueprint("positions", __name__)


RANGE_OPTIONS = [
    ("1d", "Last 24h", 1),
    ("7d", "Last 7 days", 7),
    ("30d", "Last 30 days", 30),
    ("90d", "Last 90 days", 90),
    ("all", "All time", None),
]


def _range_cutoff(range_key: str):
    """Return ISO cutoff string for the chosen range, or None for 'all'."""
    for key, _label, days in RANGE_OPTIONS:
        if key == range_key:
            if days is None:
                return None
            return (datetime.now() - timedelta(days=days)).isoformat(sep=" ")
    return None


def _resolve_filter(args):
    """`hours` overrides `range` when set to a positive number.
    Returns (cutoff_iso_or_None, active_range_or_None, active_hours_or_None)."""
    hours_arg = (args.get("hours") or "").strip()
    if hours_arg:
        try:
            h = float(hours_arg)
        except ValueError:
            h = 0.0
        if h > 0:
            cutoff = (datetime.now() - timedelta(hours=h)).isoformat(sep=" ")
            return cutoff, None, h
    range_key = args.get("range", "7d")
    if range_key not in {k for k, _l, _d in RANGE_OPTIONS}:
        range_key = "7d"
    return _range_cutoff(range_key), range_key, None


def _latest_prices() -> dict[str, float]:
    rows = query("""
        SELECT d.ticker, d.last_price
        FROM daily_holding_prices d
        INNER JOIN (
            SELECT ticker, MAX(date || ' ' || updated_at) AS m
            FROM daily_holding_prices GROUP BY ticker
        ) latest
          ON d.ticker = latest.ticker
         AND (d.date || ' ' || d.updated_at) = latest.m
        """)
    return {r["ticker"]: r["last_price"] for r in rows}


def _latest_scores() -> dict[str, dict]:
    rows = query("""
        SELECT s.ticker, s.composite_score, s.scored_at
        FROM scores s
        INNER JOIN (
            SELECT ticker, MAX(scored_at) AS m
            FROM scores GROUP BY ticker
        ) latest ON s.ticker = latest.ticker AND s.scored_at = latest.m
        """)
    return {
        r["ticker"]: {
            "composite_score": r["composite_score"],
            "scored_at": r["scored_at"],
        }
        for r in rows
    }


@positions_bp.route("/")
def index():
    cutoff, range_key, active_hours = _resolve_filter(request.args)

    if cutoff:
        open_positions = query(
            "SELECT * FROM positions WHERE status='open' AND entry_time >= ? "
            "ORDER BY entry_time DESC",
            (cutoff,),
        )
        closed_positions = query(
            "SELECT * FROM positions WHERE status='closed' AND exit_time >= ? "
            "ORDER BY exit_time DESC",
            (cutoff,),
        )
    else:
        open_positions = query(
            "SELECT * FROM positions WHERE status='open' ORDER BY entry_time DESC"
        )
        closed_positions = query(
            "SELECT * FROM positions WHERE status='closed' ORDER BY exit_time DESC"
        )

    prices = _latest_prices()
    scores = _latest_scores()

    # Decorate open positions with current price / current score / pnl
    for p in open_positions:
        cur_price = prices.get(p["ticker"])
        p["current_price"] = cur_price
        latest = scores.get(p["ticker"])
        p["current_score"] = latest["composite_score"] if latest else None
        p["current_score_at"] = latest["scored_at"] if latest else None
        if cur_price is not None and p.get("entry_price"):
            p["pnl_value"] = (cur_price - p["entry_price"]) * (p["qty"] or 0)
            p["pnl_pct"] = (
                (cur_price - p["entry_price"]) / p["entry_price"] * 100
                if p["entry_price"]
                else 0.0
            )
        else:
            p["pnl_value"] = None
            p["pnl_pct"] = None

    return render_template(
        "positions.html",
        open_positions=open_positions,
        closed_positions=closed_positions,
        range_options=RANGE_OPTIONS,
        active_range=range_key,
        active_hours=active_hours,
    )
