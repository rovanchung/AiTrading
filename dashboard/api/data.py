"""JSON API endpoints for charts and tables."""

import json
from flask import Blueprint, jsonify, request
from dashboard.db import query, query_one

api_bp = Blueprint("api", __name__)

SECTOR_COLORS = [
    "#3b82f6",
    "#22c55e",
    "#eab308",
    "#ef4444",
    "#a855f7",
    "#ec4899",
    "#06b6d4",
    "#f97316",
    "#14b8a6",
    "#8b5cf6",
    "#64748b",
]


@api_bp.route("/overview")
def overview():
    """Dashboard KPI summary."""
    snapshot = query_one(
        "SELECT * FROM portfolio_snapshots ORDER BY snapshot_time DESC LIMIT 1"
    )
    closed = query(
        "SELECT pnl FROM positions WHERE status='closed' AND pnl IS NOT NULL"
    )
    open_positions = query(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(qty * entry_price), 0) as value "
        "FROM positions WHERE status='open'"
    )

    total_pnl = sum(r["pnl"] for r in closed)
    wins = sum(1 for r in closed if r["pnl"] > 0)
    total_trades = len(closed)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    return jsonify(
        {
            "portfolio_value": snapshot["total_value"] if snapshot else 0,
            "cash": snapshot["cash"] if snapshot else 0,
            "positions_value": snapshot["positions_value"] if snapshot else 0,
            "peak_value": snapshot["peak_value"] if snapshot else 0,
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "total_trades": total_trades,
            "open_count": open_positions[0]["cnt"] if open_positions else 0,
        }
    )


@api_bp.route("/portfolio-history")
def portfolio_history():
    """Portfolio value over time for line charts."""
    rows = query(
        "SELECT snapshot_time, total_value, cash, positions_value, peak_value "
        "FROM portfolio_snapshots ORDER BY snapshot_time ASC"
    )
    return jsonify(
        {
            "labels": [r["snapshot_time"] for r in rows],
            "total_value": [r["total_value"] for r in rows],
            "cash": [r["cash"] for r in rows],
            "positions_value": [r["positions_value"] for r in rows],
            "peak_value": [r["peak_value"] for r in rows],
        }
    )


@api_bp.route("/drawdown")
def drawdown():
    """Drawdown percentage over time."""
    rows = query(
        "SELECT snapshot_time, total_value, peak_value "
        "FROM portfolio_snapshots ORDER BY snapshot_time ASC"
    )
    labels = []
    dd_pct = []
    for r in rows:
        labels.append(r["snapshot_time"])
        if r["peak_value"] and r["peak_value"] > 0:
            dd_pct.append((r["total_value"] - r["peak_value"]) / r["peak_value"] * 100)
        else:
            dd_pct.append(0)
    return jsonify({"labels": labels, "drawdown_pct": dd_pct})


@api_bp.route("/sector-allocation")
def sector_allocation():
    """Sector allocation from open positions."""
    rows = query(
        "SELECT sector, SUM(qty * entry_price) as allocation "
        "FROM positions WHERE status='open' AND sector != '' "
        "GROUP BY sector ORDER BY allocation DESC"
    )
    return jsonify(
        {
            "labels": [r["sector"] for r in rows],
            "values": [r["allocation"] for r in rows],
            "colors": SECTOR_COLORS[: len(rows)],
        }
    )


@api_bp.route("/score-radar/<ticker>")
def score_radar(ticker):
    """Score breakdown for radar chart."""
    row = query_one(
        "SELECT technical_score, fundamental_score, momentum_score, sentiment_score, "
        "composite_score, details FROM scores WHERE ticker = ? "
        "ORDER BY scored_at DESC LIMIT 1",
        (ticker.upper(),),
    )
    if not row:
        return jsonify({"labels": [], "values": [], "composite": 0})
    return jsonify(
        {
            "labels": ["Technical", "Fundamental", "Momentum", "Sentiment"],
            "values": [
                row["technical_score"],
                row["fundamental_score"],
                row["momentum_score"],
                row["sentiment_score"],
            ],
            "composite": row["composite_score"],
            "details": json.loads(row["details"]) if row["details"] else {},
        }
    )


@api_bp.route("/price-history/<ticker>")
def price_history(ticker):
    """Observed-price history for a ticker, built from existing data only.

    Two sources are merged (no new data is stored):
      * `orders` — every filled buy/sell gives the market price at that instant
        (`filled_price` @ `filled_at`); this is the dense intraday series.
      * `daily_holding_prices` — one close per day the ticker was held, which
        bridges days with no trades. Its date is pinned to end-of-day so it
        sorts after that day's fills.

    The legacy `price_snapshots` table is written only by the deprecated
    PositionMonitor and is empty in the active v3/v4 databases, so it is unused.
    """
    t = ticker.upper()
    rows = query(
        "SELECT snapshot_time, price FROM ("
        "  SELECT filled_at AS snapshot_time, filled_price AS price "
        "  FROM orders "
        "  WHERE ticker = ? AND status = 'filled' "
        "    AND filled_price IS NOT NULL AND filled_at IS NOT NULL "
        "  UNION ALL "
        "  SELECT date || ' 23:59:59' AS snapshot_time, last_price AS price "
        "  FROM daily_holding_prices WHERE ticker = ?"
        ") ORDER BY snapshot_time ASC",
        (t, t),
    )
    return jsonify(
        {
            "labels": [r["snapshot_time"] for r in rows],
            "prices": [r["price"] for r in rows],
        }
    )


@api_bp.route("/positions")
def positions_data():
    """Positions as JSON for DataTables."""
    status = request.args.get("status", "open")
    if status == "all":
        rows = query("SELECT * FROM positions ORDER BY entry_time DESC")
    else:
        rows = query(
            "SELECT * FROM positions WHERE status = ? ORDER BY entry_time DESC",
            (status,),
        )
    return jsonify({"data": rows})


@api_bp.route("/rankings")
def rankings_data():
    """Latest scores per ticker, ranked by composite."""
    rows = query("""
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
    return jsonify({"data": rows})


@api_bp.route("/orders")
def orders_data():
    """Orders as JSON for DataTables."""
    rows = query("SELECT * FROM orders ORDER BY submitted_at DESC")
    return jsonify({"data": rows})


@api_bp.route("/fundamentals/<ticker>")
def fundamentals_data(ticker):
    """Fundamental data for a ticker."""
    row = query_one("SELECT * FROM fundamentals WHERE ticker = ?", (ticker.upper(),))
    return jsonify(row or {})


@api_bp.route("/peer-scores")
def peer_scores():
    """Composite scores of all tickers at a given moment, sorted desc.

    Query params:
        at: ISO timestamp anchoring the lookup. If omitted, returns the
            latest score per ticker (current-moment ranking).
        window_minutes: ± window around `at` to constrain scan-cycle scope
            (default 10). Each ticker contributes at most one row — the
            score whose scored_at is closest to `at` within the window.
        highlight: optional ticker to surface explicitly in the response.
    """
    at = request.args.get("at")
    highlight = (request.args.get("highlight") or "").upper().strip() or None
    try:
        window = int(request.args.get("window_minutes", 10))
    except (TypeError, ValueError):
        window = 10
    window = max(1, min(window, 1440))

    if at:
        rows = query(
            f"""
            WITH ranked AS (
                SELECT s.ticker, s.composite_score, s.scored_at,
                       ABS(julianday(s.scored_at) - julianday(?)) AS delta,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.ticker
                           ORDER BY ABS(julianday(s.scored_at) - julianday(?))
                       ) AS rn
                FROM scores s
                WHERE s.scored_at BETWEEN datetime(?, '-{window} minutes')
                                       AND datetime(?, '+{window} minutes')
            )
            SELECT ticker, composite_score, scored_at
            FROM ranked
            WHERE rn = 1
            ORDER BY composite_score DESC, ticker ASC
            """,
            (at, at, at, at),
        )
    else:
        rows = query("""
            SELECT s.ticker, s.composite_score, s.scored_at
            FROM scores s
            INNER JOIN (
                SELECT ticker, MAX(scored_at) AS max_scored
                FROM scores GROUP BY ticker
            ) latest
              ON s.ticker = latest.ticker AND s.scored_at = latest.max_scored
            ORDER BY s.composite_score DESC, s.ticker ASC
            """)

    return jsonify({"at": at, "highlight": highlight, "data": rows})


@api_bp.route("/pnl-by-day")
def pnl_by_day():
    """Per-day realized P&L of closed trades, split into profit and loss.

    Grouped by the exit date so the portfolio page can draw one green
    (total profit) and one red (total loss) bar per day. `loss` is returned
    as a negative number so it plots below zero.
    """
    rows = query(
        "SELECT date(exit_time) AS d, "
        "SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) AS profit, "
        "SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) AS loss "
        "FROM positions "
        "WHERE status='closed' AND pnl IS NOT NULL AND exit_time IS NOT NULL "
        "GROUP BY date(exit_time) ORDER BY d ASC"
    )
    return jsonify(
        {
            "labels": [r["d"] for r in rows],
            "profit": [r["profit"] for r in rows],
            "loss": [r["loss"] for r in rows],
        }
    )
