"""SQLite database management for AiTrading."""

import sqlite3
import json
from datetime import datetime, timedelta
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

CREATE INDEX IF NOT EXISTS idx_scores_ticker_scored_at
    ON scores(ticker, scored_at DESC);

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
    sector TEXT,
    entry_score REAL,
    exit_score REAL
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

CREATE TABLE IF NOT EXISTS daily_holding_prices (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    last_price REAL NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_time TIMESTAMP,
    total_value REAL,
    cash REAL,
    positions_value REAL,
    peak_value REAL
);

CREATE TABLE IF NOT EXISTS trade_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT,
    qty INTEGER,
    entry_price REAL,
    entry_time TIMESTAMP,
    exit_price REAL,
    exit_time TIMESTAMP,
    pnl REAL,
    pnl_pct REAL,
    hold_duration_minutes REAL,
    exit_reason TEXT,
    sector TEXT,
    position_id INTEGER
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
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Add columns that may be missing on legacy databases."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(positions)")}
        if "entry_score" not in cols:
            self.conn.execute("ALTER TABLE positions ADD COLUMN entry_score REAL")
        if "exit_score" not in cols:
            self.conn.execute("ALTER TABLE positions ADD COLUMN exit_score REAL")

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
                score.ticker,
                score.scored_at,
                score.technical,
                score.fundamental,
                score.momentum,
                score.sentiment,
                score.composite,
                json.dumps(score.details),
            ),
        )
        self.conn.commit()

    def get_recent_composite_scores(self, ticker: str, limit: int) -> list[float]:
        """Return the last `limit` composite scores for ticker, newest first.
        Used by the no_longer_qualifies debouncing logic in PortfolioManager."""
        if limit <= 0:
            return []
        rows = self.conn.execute(
            """SELECT composite_score FROM scores
               WHERE ticker = ?
               ORDER BY scored_at DESC LIMIT ?""",
            (ticker, limit),
        ).fetchall()
        return [float(r["composite_score"]) for r in rows]

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
        entry_score = pos.entry_score
        if entry_score is None:
            entry_score = self._latest_composite(pos.ticker)
        cur = self.conn.execute(
            """INSERT INTO positions
               (ticker, qty, entry_price, entry_time, stop_loss, take_profit,
                high_water_mark, status, sector, entry_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pos.ticker,
                pos.qty,
                pos.entry_price,
                pos.entry_time,
                pos.stop_loss,
                pos.take_profit,
                pos.high_water_mark,
                pos.status,
                pos.sector,
                entry_score,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def _latest_composite(self, ticker: str) -> Optional[float]:
        """Most recent composite score recorded for `ticker`, or None."""
        row = self.conn.execute(
            "SELECT composite_score FROM scores WHERE ticker = ? "
            "ORDER BY scored_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        if not row or row["composite_score"] is None:
            return None
        return float(row["composite_score"])

    def get_open_positions(self) -> list[Position]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE status = 'open'"
        ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def reconcile_positions(
        self,
        alpaca_positions: list[dict],
        cooldown_tickers: set[str] | None = None,
        pending_sell_tickers: set[str] | None = None,
    ) -> dict:
        """Sync local positions table to Alpaca's ground truth.

        - Insert open rows for tickers Alpaca holds but the DB does not
          (cost basis = avg_entry, entry_time = now since the true entry
          time is unrecoverable).
        - Update qty / entry_price on rows where they drift from Alpaca.
        - Mark rows closed when Alpaca no longer holds the ticker; uses the
          last-known entry_price as exit_price (true exit price is unknown
          at this point, but pnl will be reconciled to ~0 — better than
          letting the row persist as phantom-open).

        `cooldown_tickers` (optional): names within their post-exit cooldown
        window. An untracked Alpaca holding for such a name is an unwanted
        re-entry (a position can only become untracked-and-cooled by being
        re-bought after a profit/loss sell). It is NOT inserted; instead its
        ticker is returned under "cooldown_holdings" so the caller can flatten
        it. This keeps the cooldown enforced on the drift path, not just at
        signal generation.

        `pending_sell_tickers` (optional): names whose exit (sell) order is
        still in flight. Alpaca keeps showing the shares until the sell fills,
        but the DB position was already closed when the exit was issued.
        Re-inserting it here would reset entry_time and re-arm the loss-cut
        that triggered the exit, so such a name is skipped entirely (neither
        tracked nor flattened) — the in-flight sell is left to complete.
        Checked before cooldown so a just-exited name isn't double-sold.

        Returns a counts dict for logging. Only touches rows with status='open'.
        """
        cooldown_tickers = cooldown_tickers or set()
        pending_sell_tickers = pending_sell_tickers or set()
        local_rows = self.conn.execute(
            "SELECT id, ticker, qty, entry_price FROM positions WHERE status='open'"
        ).fetchall()
        local_by_ticker = {r["ticker"]: dict(r) for r in local_rows}
        alpaca_by_ticker = {p["ticker"]: p for p in alpaca_positions}

        inserted = updated = closed = 0
        cooldown_holdings: list[str] = []
        now = datetime.now()

        # Insert / update
        for ticker, ap in alpaca_by_ticker.items():
            local = local_by_ticker.get(ticker)
            if local is None:
                if ticker in pending_sell_tickers:
                    # Exit order still in flight — leave the holding alone so
                    # the pending sell can complete; don't re-track or flatten.
                    continue
                if ticker in cooldown_tickers:
                    # Unwanted re-entry of a cooled-down name — don't track it;
                    # let the caller flatten the stray holding.
                    cooldown_holdings.append(ticker)
                    continue
                sector = self.get_stock_sector(ticker)
                entry_score = self._latest_composite(ticker)
                self.conn.execute(
                    """INSERT INTO positions
                       (ticker, qty, entry_price, entry_time, status, sector,
                        entry_score)
                       VALUES (?, ?, ?, ?, 'open', ?, ?)""",
                    (ticker, ap["qty"], ap["avg_entry"], now, sector, entry_score),
                )
                inserted += 1
            elif (
                local["qty"] != ap["qty"]
                or abs(local["entry_price"] - ap["avg_entry"]) > 0.001
            ):
                self.conn.execute(
                    "UPDATE positions SET qty=?, entry_price=? WHERE id=?",
                    (ap["qty"], ap["avg_entry"], local["id"]),
                )
                updated += 1

        # Close orphans (DB shows open, Alpaca doesn't hold it)
        for ticker, local in local_by_ticker.items():
            if ticker not in alpaca_by_ticker:
                self.close_position(
                    local["id"], local["entry_price"], "reconcile_orphan"
                )
                closed += 1

        if inserted or updated or closed:
            self.conn.commit()
        return {
            "inserted": inserted,
            "updated": updated,
            "closed": closed,
            "cooldown_holdings": cooldown_holdings,
        }

    def update_position(self, pos_id: int, **kwargs):
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [pos_id]
        self.conn.execute(f"UPDATE positions SET {sets} WHERE id = ?", vals)
        self.conn.commit()

    def mark_position_synced_closed(self, pos_id: int):
        """Close a local open position that no longer exists on Alpaca.

        The real exit happened outside this system, so we don't know the
        exit price. Skip PnL computation and trade_history to avoid
        polluting performance stats with fabricated trades.
        """
        self.conn.execute(
            """UPDATE positions
               SET status = 'closed',
                   exit_reason = 'synced_external',
                   exit_time = ?
               WHERE id = ?""",
            (datetime.now(), pos_id),
        )
        self.conn.commit()

    def close_position(self, pos_id: int, exit_price: float, reason: str):
        entry_row = self.conn.execute(
            "SELECT * FROM positions WHERE id = ?", (pos_id,)
        ).fetchone()
        pnl = (exit_price - entry_row["entry_price"]) * entry_row["qty"]
        now = datetime.now()
        exit_score = self._latest_composite(entry_row["ticker"])
        self.conn.execute(
            """UPDATE positions SET exit_price=?, exit_time=?, status='closed',
               exit_reason=?, pnl=?, exit_score=? WHERE id=?""",
            (exit_price, now, reason, pnl, exit_score, pos_id),
        )
        # Record in trade_history
        entry_time = (
            datetime.fromisoformat(entry_row["entry_time"])
            if entry_row["entry_time"]
            else now
        )
        hold_minutes = (now - entry_time).total_seconds() / 60
        pnl_pct = (
            ((exit_price - entry_row["entry_price"]) / entry_row["entry_price"] * 100)
            if entry_row["entry_price"] > 0
            else 0
        )
        self.conn.execute(
            """INSERT INTO trade_history
               (ticker, qty, entry_price, entry_time, exit_price, exit_time,
                pnl, pnl_pct, hold_duration_minutes, exit_reason, sector, position_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_row["ticker"],
                entry_row["qty"],
                entry_row["entry_price"],
                entry_row["entry_time"],
                exit_price,
                now,
                pnl,
                round(pnl_pct, 2),
                round(hold_minutes, 1),
                reason,
                entry_row["sector"],
                pos_id,
            ),
        )
        self.conn.commit()

    def _row_to_position(self, row) -> Position:
        keys = row.keys() if hasattr(row, "keys") else []
        return Position(
            id=row["id"],
            ticker=row["ticker"],
            qty=row["qty"],
            entry_price=row["entry_price"],
            entry_time=(
                datetime.fromisoformat(row["entry_time"]) if row["entry_time"] else None
            ),
            exit_price=row["exit_price"],
            exit_time=(
                datetime.fromisoformat(row["exit_time"]) if row["exit_time"] else None
            ),
            stop_loss=row["stop_loss"],
            take_profit=row["take_profit"],
            high_water_mark=row["high_water_mark"],
            status=row["status"],
            exit_reason=row["exit_reason"] or "",
            pnl=row["pnl"] or 0.0,
            sector=row["sector"] or "",
            entry_score=row["entry_score"] if "entry_score" in keys else None,
            exit_score=row["exit_score"] if "exit_score" in keys else None,
        )

    def get_recent_losers(self, hours: float = 24) -> set[str]:
        """Return tickers that were closed at a loss within the last N hours."""
        # exit_time is stored from datetime.now() (local). SQLite's
        # datetime('now') is UTC, so compute the cutoff in Python to keep
        # both sides of the comparison in the same timezone.
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(sep=" ")
        rows = self.conn.execute(
            """SELECT DISTINCT ticker FROM positions
               WHERE status = 'closed' AND pnl < 0
                 AND exit_time >= ?""",
            (cutoff,),
        ).fetchall()
        return {r["ticker"] for r in rows}

    def get_recently_profit_sold(self, hours: float = 2) -> set[str]:
        """Return tickers sold for profit/loss reasons within the last N hours.
        Does NOT include redistribution or rebalancing sells."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(sep=" ")
        rows = self.conn.execute(
            """SELECT DISTINCT ticker FROM positions
               WHERE status = 'closed'
                 AND exit_time >= ?
                 AND (exit_reason LIKE 'profit_take%' OR exit_reason LIKE 'loss_cut%')""",
            (cutoff,),
        ).fetchall()
        return {r["ticker"] for r in rows}

    # --- Fundamentals ---
    _FUNDAMENTALS_COLUMNS = {
        "eps_ttm",
        "eps_annual",
        "book_value_per_share_quarterly",
        "book_value_per_share_annual",
        "earnings_growth_ttm",
        "earnings_growth_5y",
        "roe_ttm",
        "roe_annual",
        "net_margin_ttm",
        "gross_margin_ttm",
        "operating_margin_ttm",
        "revenue_growth_ttm_yoy",
        "revenue_growth_3y",
        "revenue_growth_5y",
        "current_ratio_quarterly",
        "current_ratio_annual",
        "debt_to_equity_annual",
        "free_cash_flow_ttm",
        "fcf_per_share_ttm",
    }

    def upsert_fundamentals(
        self, ticker: str, data: dict, provider: str, raw_json: str = ""
    ):
        """Insert or update fundamental data for a ticker."""
        # Filter to valid columns only
        filtered = {
            k: v
            for k, v in data.items()
            if k in self._FUNDAMENTALS_COLUMNS and v is not None
        }
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
                order.alpaca_order_id,
                order.ticker,
                order.side,
                order.qty,
                order.order_type,
                order.limit_price,
                order.status,
                order.submitted_at,
                order.filled_at,
                order.filled_price,
                order.error_message,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_order(self, order_id: int, **kwargs):
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [order_id]
        self.conn.execute(f"UPDATE orders SET {sets} WHERE id = ?", vals)
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

    def get_pending_sell_orders(self) -> list[dict]:
        """Get sell orders that haven't been filled or failed yet."""
        rows = self.conn.execute("""SELECT id, alpaca_order_id, ticker, qty, status
               FROM orders
               WHERE side = 'sell'
                 AND status NOT IN ('filled', 'failed', 'canceled', 'cancelled')
                 AND alpaca_order_id IS NOT NULL
                 AND alpaca_order_id != ''
               ORDER BY submitted_at DESC""").fetchall()
        return [dict(r) for r in rows]

    # --- Portfolio Snapshots ---
    def save_portfolio_snapshot(
        self, total_value: float, cash: float, positions_value: float, peak_value: float
    ):
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

    # --- Daily Holding Prices ---
    def record_daily_holding_prices(self, alpaca_positions: list[dict]):
        """Upsert today's last observed price for every open Alpaca position.

        Called every rebalance cycle; the final upsert of the day becomes the
        day's closing price for the ticker. Used by prior-close profit/loss
        comparisons in PortfolioManager.
        """
        if not alpaca_positions:
            return
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        rows = []
        for p in alpaca_positions:
            price = p.get("current_price")
            ticker = p.get("ticker")
            if not ticker or price is None or price <= 0:
                continue
            rows.append((ticker, today, float(price), now))
        if not rows:
            return
        self.conn.executemany(
            """INSERT INTO daily_holding_prices (ticker, date, last_price, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(ticker, date) DO UPDATE SET
                 last_price = excluded.last_price,
                 updated_at = excluded.updated_at""",
            rows,
        )
        self.conn.commit()

    def get_prior_close(self, ticker: str) -> Optional[float]:
        """Return the most recent recorded last_price for ticker on a date
        before today. None if no such record exists."""
        today = datetime.now().strftime("%Y-%m-%d")
        row = self.conn.execute(
            """SELECT last_price FROM daily_holding_prices
               WHERE ticker = ? AND date < ?
               ORDER BY date DESC LIMIT 1""",
            (ticker, today),
        ).fetchone()
        if not row or row["last_price"] is None:
            return None
        return float(row["last_price"])
