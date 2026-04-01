"""SQLite database management for AiTrading."""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import Config
from core.models import Position, Order, ScoreResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS universe (
    ticker TEXT PRIMARY KEY,
    name TEXT,
    sector TEXT,
    market_cap REAL,
    last_updated TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_time TIMESTAMP,
    ticker TEXT,
    price REAL,
    volume INTEGER,
    passed_filters TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT,
    scored_at TIMESTAMP,
    technical_score REAL,
    fundamental_score REAL,
    momentum_score REAL,
    sentiment_score REAL,
    composite_score REAL,
    details TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT,
    qty INTEGER,
    entry_price REAL,
    entry_time TIMESTAMP,
    exit_price REAL,
    exit_time TIMESTAMP,
    stop_loss REAL,
    take_profit REAL,
    high_water_mark REAL,
    status TEXT DEFAULT 'open',
    exit_reason TEXT,
    pnl REAL,
    sector TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alpaca_order_id TEXT,
    ticker TEXT,
    side TEXT,
    qty INTEGER,
    order_type TEXT,
    limit_price REAL,
    status TEXT,
    submitted_at TIMESTAMP,
    filled_at TIMESTAMP,
    filled_price REAL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT,
    price REAL,
    snapshot_time TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_time TIMESTAMP,
    total_value REAL,
    cash REAL,
    positions_value REAL,
    peak_value REAL
);

CREATE TABLE IF NOT EXISTS fundamentals (
    ticker TEXT PRIMARY KEY,
    eps_ttm REAL,
    eps_annual REAL,
    book_value_per_share_quarterly REAL,
    book_value_per_share_annual REAL,
    earnings_growth_ttm REAL,
    earnings_growth_5y REAL,
    roe_ttm REAL,
    roe_annual REAL,
    net_margin_ttm REAL,
    gross_margin_ttm REAL,
    operating_margin_ttm REAL,
    revenue_growth_ttm_yoy REAL,
    revenue_growth_3y REAL,
    revenue_growth_5y REAL,
    current_ratio_quarterly REAL,
    current_ratio_annual REAL,
    debt_to_equity_annual REAL,
    free_cash_flow_ttm REAL,
    fcf_per_share_ttm REAL,
    provider TEXT,
    updated_at TIMESTAMP,
    raw_json TEXT
);
"""


class Database:
    """SQLite database wrapper for AiTrading."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")

    def init_schema(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # --- Universe ---
    def upsert_universe(self, ticker: str, name: str, sector: str, market_cap: float):
        self.conn.execute(
            """INSERT INTO universe (ticker, name, sector, market_cap, last_updated)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 name=excluded.name, sector=excluded.sector,
                 market_cap=excluded.market_cap, last_updated=excluded.last_updated""",
            (ticker, name, sector, market_cap, datetime.now()),
        )
        self.conn.commit()

    def get_universe_age_days(self) -> Optional[float]:
        row = self.conn.execute(
            "SELECT MIN(last_updated) as oldest FROM universe"
        ).fetchone()
        if row and row["oldest"]:
            oldest = datetime.fromisoformat(row["oldest"])
            return (datetime.now() - oldest).total_seconds() / 86400
        return None

    def get_universe_tickers(self) -> list[str]:
        rows = self.conn.execute("SELECT ticker FROM universe").fetchall()
        return [r["ticker"] for r in rows]

    def get_stock_sector(self, ticker: str) -> str:
        row = self.conn.execute(
            "SELECT sector FROM universe WHERE ticker = ?", (ticker,)
        ).fetchone()
        return row["sector"] if row else ""

    # --- Scores ---
    def save_score(self, score: ScoreResult):
        self.conn.execute(
            """INSERT INTO scores
               (ticker, scored_at, technical_score, fundamental_score,
                momentum_score, sentiment_score, composite_score, details)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                score.ticker, score.scored_at, score.technical,
                score.fundamental, score.momentum, score.sentiment,
                score.composite, json.dumps(score.details),
            ),
        )
        self.conn.commit()

    def get_latest_score(self, ticker: str) -> Optional[ScoreResult]:
        row = self.conn.execute(
            """SELECT * FROM scores WHERE ticker = ?
               ORDER BY scored_at DESC LIMIT 1""",
            (ticker,),
        ).fetchone()
        if not row:
            return None
        return ScoreResult(
            ticker=row["ticker"],
            technical=row["technical_score"],
            fundamental=row["fundamental_score"],
            momentum=row["momentum_score"],
            sentiment=row["sentiment_score"],
            composite=row["composite_score"],
            details=json.loads(row["details"]) if row["details"] else {},
            scored_at=datetime.fromisoformat(row["scored_at"]),
        )

    # --- Positions ---
    def save_position(self, pos: Position) -> int:
        cur = self.conn.execute(
            """INSERT INTO positions
               (ticker, qty, entry_price, entry_time, stop_loss, take_profit,
                high_water_mark, status, sector)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pos.ticker, pos.qty, pos.entry_price, pos.entry_time,
                pos.stop_loss, pos.take_profit, pos.high_water_mark,
                pos.status, pos.sector,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_open_positions(self) -> list[Position]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE status = 'open'"
        ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def update_position(self, pos_id: int, **kwargs):
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [pos_id]
        self.conn.execute(
            f"UPDATE positions SET {sets} WHERE id = ?", vals
        )
        self.conn.commit()

    def close_position(self, pos_id: int, exit_price: float, reason: str):
        entry_row = self.conn.execute(
            "SELECT entry_price, qty FROM positions WHERE id = ?", (pos_id,)
        ).fetchone()
        pnl = (exit_price - entry_row["entry_price"]) * entry_row["qty"]
        self.conn.execute(
            """UPDATE positions SET exit_price=?, exit_time=?, status='closed',
               exit_reason=?, pnl=? WHERE id=?""",
            (exit_price, datetime.now(), reason, pnl, pos_id),
        )
        self.conn.commit()

    def _row_to_position(self, row) -> Position:
        return Position(
            id=row["id"],
            ticker=row["ticker"],
            qty=row["qty"],
            entry_price=row["entry_price"],
            entry_time=datetime.fromisoformat(row["entry_time"]) if row["entry_time"] else None,
            exit_price=row["exit_price"],
            exit_time=datetime.fromisoformat(row["exit_time"]) if row["exit_time"] else None,
            stop_loss=row["stop_loss"],
            take_profit=row["take_profit"],
            high_water_mark=row["high_water_mark"],
            status=row["status"],
            exit_reason=row["exit_reason"] or "",
            pnl=row["pnl"] or 0.0,
            sector=row["sector"] or "",
        )

    def get_recent_losers(self, hours: float = 24) -> set[str]:
        """Return tickers that were closed at a loss within the last N hours."""
        rows = self.conn.execute(
            """SELECT DISTINCT ticker FROM positions
               WHERE status = 'closed' AND pnl < 0
                 AND exit_time >= datetime('now', ? || ' hours')""",
            (f"-{hours}",),
        ).fetchall()
        return {r["ticker"] for r in rows}

    def get_recently_profit_sold(self, hours: float = 2) -> set[str]:
        """Return tickers sold for profit/loss reasons within the last N hours.
        Does NOT include redistribution or rebalancing sells."""
        rows = self.conn.execute(
            """SELECT DISTINCT ticker FROM positions
               WHERE status = 'closed'
                 AND exit_time >= datetime('now', ? || ' hours')
                 AND (exit_reason LIKE 'profit_take%' OR exit_reason LIKE 'loss_cut%')""",
            (f"-{hours}",),
        ).fetchall()
        return {r["ticker"] for r in rows}

    # --- Fundamentals ---
    _FUNDAMENTALS_COLUMNS = {
        "eps_ttm", "eps_annual", "book_value_per_share_quarterly",
        "book_value_per_share_annual", "earnings_growth_ttm", "earnings_growth_5y",
        "roe_ttm", "roe_annual", "net_margin_ttm", "gross_margin_ttm",
        "operating_margin_ttm", "revenue_growth_ttm_yoy", "revenue_growth_3y",
        "revenue_growth_5y", "current_ratio_quarterly", "current_ratio_annual",
        "debt_to_equity_annual", "free_cash_flow_ttm", "fcf_per_share_ttm",
    }

    def upsert_fundamentals(self, ticker: str, data: dict, provider: str,
                            raw_json: str = ""):
        """Insert or update fundamental data for a ticker."""
        # Filter to valid columns only
        filtered = {k: v for k, v in data.items()
                    if k in self._FUNDAMENTALS_COLUMNS and v is not None}
        if not filtered:
            return

        cols = ["ticker", "provider", "updated_at", "raw_json"] + list(filtered.keys())
        vals = [ticker, provider, datetime.now(), raw_json] + list(filtered.values())
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "ticker")

        self.conn.execute(
            f"""INSERT INTO fundamentals ({', '.join(cols)})
                VALUES ({placeholders})
                ON CONFLICT(ticker) DO UPDATE SET {updates}""",
            vals,
        )
        self.conn.commit()

    def get_fundamentals(self, ticker: str) -> Optional[dict]:
        """Get stored fundamental data for a ticker. Returns None if not found."""
        row = self.conn.execute(
            "SELECT * FROM fundamentals WHERE ticker = ?", (ticker,)
        ).fetchone()
        if not row:
            return None
        return {k: row[k] for k in row.keys() if row[k] is not None}

    def get_fundamentals_age_days(self, ticker: str) -> Optional[float]:
        """Return days since fundamentals were last updated, or None if no record."""
        row = self.conn.execute(
            "SELECT updated_at FROM fundamentals WHERE ticker = ?", (ticker,)
        ).fetchone()
        if not row or not row["updated_at"]:
            return None
        updated = datetime.fromisoformat(row["updated_at"])
        return (datetime.now() - updated).total_seconds() / 86400

    # --- Orders ---
    def save_order(self, order: Order) -> int:
        cur = self.conn.execute(
            """INSERT INTO orders
               (alpaca_order_id, ticker, side, qty, order_type, limit_price,
                status, submitted_at, filled_at, filled_price, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order.alpaca_order_id, order.ticker, order.side, order.qty,
                order.order_type, order.limit_price, order.status,
                order.submitted_at, order.filled_at, order.filled_price,
                order.error_message,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_order(self, order_id: int, **kwargs):
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [order_id]
        self.conn.execute(
            f"UPDATE orders SET {sets} WHERE id = ?", vals
        )
        self.conn.commit()

    def get_pending_buy_orders(self) -> list[dict]:
        """Get buy orders that haven't been filled or failed yet."""
        rows = self.conn.execute(
            """SELECT id, alpaca_order_id, ticker, qty, limit_price, status
               FROM orders
               WHERE side = 'buy'
                 AND status NOT IN ('filled', 'failed', 'canceled', 'cancelled')
                 AND alpaca_order_id IS NOT NULL
                 AND alpaca_order_id != ''
               ORDER BY submitted_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Portfolio Snapshots ---
    def save_portfolio_snapshot(self, total_value: float, cash: float,
                                positions_value: float, peak_value: float):
        self.conn.execute(
            """INSERT INTO portfolio_snapshots
               (snapshot_time, total_value, cash, positions_value, peak_value)
               VALUES (?, ?, ?, ?, ?)""",
            (datetime.now(), total_value, cash, positions_value, peak_value),
        )
        self.conn.commit()

    def get_peak_value(self) -> float:
        row = self.conn.execute(
            "SELECT MAX(peak_value) as peak FROM portfolio_snapshots"
        ).fetchone()
        return row["peak"] if row and row["peak"] else 0.0

    # --- Price Snapshots ---
    def save_price_snapshot(self, ticker: str, price: float):
        self.conn.execute(
            "INSERT INTO price_snapshots (ticker, price, snapshot_time) VALUES (?, ?, ?)",
            (ticker, price, datetime.now()),
        )
        self.conn.commit()
