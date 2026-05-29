"""Dashboard home page."""

from flask import Blueprint, current_app, render_template, session

from dashboard.db import prior_close_map, query, query_one

main_bp = Blueprint("main", __name__)


def _active_trading_params() -> dict:
    """Trading params for the active dashboard version, merged the same way
    `core.config.load_config` merges them (account block overrides defaults)."""
    cfg = current_app.config.get("TRADING_CONFIG", {}) or {}
    trading = dict(cfg.get("trading", {}) or {})
    version = session.get("dashboard_version")
    if version:
        accounts = cfg.get("accounts", {}) or {}
        acct = accounts.get(version, {}) or {}
        reserved = {"database_path", "account_overrides"}
        for k, v in acct.items():
            if k not in reserved:
                trading[k] = v
    return trading


def _sell_trigger_prices(p: dict, params: dict, prior_close):
    """Compute the four trigger prices that the live strategy uses.
    Returns (profit_take, loss_cut, profit_take_vs_prior, loss_cut_vs_prior).
    Any pct configured as 0 yields None for that price."""
    pt_pct = params.get("v2_profit_take_pct", 0.0) or 0.0
    lc_pct = params.get("v2_loss_cut_pct", 0.0) or 0.0
    pt_prior_pct = params.get("v2_profit_take_vs_prior_close_pct", 0.0) or 0.0
    lc_prior_pct = params.get("v2_loss_cut_vs_prior_close_pct", 0.0) or 0.0

    avg = p.get("entry_price")
    pt = avg * (1 + pt_pct) if avg and pt_pct > 0 else None
    lc = avg * (1 - lc_pct) if avg and lc_pct > 0 else None
    pt_prior = (
        prior_close * (1 + pt_prior_pct) if prior_close and pt_prior_pct > 0 else None
    )
    lc_prior = (
        prior_close * (1 - lc_prior_pct) if prior_close and lc_prior_pct > 0 else None
    )
    return pt, lc, pt_prior, lc_prior


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
    recent_orders = query("SELECT * FROM orders ORDER BY submitted_at DESC LIMIT 10")
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

    params = _active_trading_params()
    prior_closes = prior_close_map([p["ticker"] for p in open_positions])
    for p in open_positions:
        pc = prior_closes.get(p["ticker"])
        p["prior_close"] = pc
        pt, lc, pt_prior, lc_prior = _sell_trigger_prices(p, params, pc)
        p["profit_take_price"] = pt
        p["loss_cut_price"] = lc
        p["profit_take_prior_price"] = pt_prior
        p["loss_cut_prior_price"] = lc_prior

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
