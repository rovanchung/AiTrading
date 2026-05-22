"""Read-only database helper for the dashboard."""

import sqlite3
from flask import g, current_app, request, session


def _active_db_path():
    """Return the DB path based on session version selection."""
    version = session.get("dashboard_version")
    if version:
        paths = current_app.config.get("DB_PATHS", {})
        if version in paths:
            return paths[version]
    return current_app.config["DB_PATH"]


def get_db():
    db_path = _active_db_path()
    # If the path changed from what's cached, close the old connection
    if "db" in g and g.get("db_path") != db_path:
        g.db.close()
        del g.db

    if "db" not in g:
        g.db = sqlite3.connect(db_path, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA query_only=ON")
        g.db_path = db_path
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    g.pop("db_path", None)
    if db is not None:
        db.close()


def init_db(app):
    app.teardown_appcontext(close_db)


def query(sql, params=()):
    """Execute a read query and return list of dicts."""
    rows = get_db().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_one(sql, params=()):
    """Execute a read query and return a single dict or None."""
    row = get_db().execute(sql, params).fetchone()
    return dict(row) if row else None


def prior_close_map(tickers):
    """Most recent recorded `last_price` on a date before today, per ticker.
    Mirrors `Database.get_prior_close` but in one query."""
    if not tickers:
        return {}
    placeholders = ",".join("?" for _ in tickers)
    rows = query(
        f"""
        SELECT d.ticker, d.last_price
        FROM daily_holding_prices d
        INNER JOIN (
            SELECT ticker, MAX(date) AS m
            FROM daily_holding_prices
            WHERE date < date('now', 'localtime')
              AND ticker IN ({placeholders})
            GROUP BY ticker
        ) latest ON d.ticker = latest.ticker AND d.date = latest.m
        """,
        tuple(tickers),
    )
    return {r["ticker"]: r["last_price"] for r in rows}
