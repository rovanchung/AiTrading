"""Read-only database helper for the dashboard."""

import sqlite3
from flask import g, current_app


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DB_PATH"],
            check_same_thread=False,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA query_only=ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
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
