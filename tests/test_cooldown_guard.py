"""Tests for cooldown enforcement on the position re-open paths.

A buy limit order placed before a name is exited can fill *after* a
profit/loss sell put it on cooldown. The cooldown is only checked at signal
generation, so without these guards the late fill (or external drift) would
re-open the position and feed the loss-cut -> rebuy loop. These tests lock in
the guards on both re-open paths.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from core.models import Position
from orchestrator.pipeline import TradingPipeline


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    d.init_schema()
    yield d
    d.close()


def _cooled_position(db, ticker="SOLV", reason="loss_cut (-2.00%)"):
    """Create a closed position so the ticker is on cooldown."""
    pid = db.save_position(
        Position(ticker=ticker, qty=10, entry_price=76.88, status="open")
    )
    db.close_position(pid, 75.30, reason)


# --- Drift path: reconcile_positions -----------------------------------------


def test_reconcile_skips_and_reports_cooled_holding(db):
    _cooled_position(db)
    assert "SOLV" in db.get_recently_profit_sold(8)

    alpaca = [
        {"ticker": "SOLV", "qty": 10, "avg_entry": 76.9, "current_price": 75.3},
        {"ticker": "AAPL", "qty": 5, "avg_entry": 100.0, "current_price": 101.0},
    ]
    recon = db.reconcile_positions(alpaca, cooldown_tickers={"SOLV"})

    # SOLV is a cooled-down re-entry: not tracked, reported for flattening.
    assert recon["cooldown_holdings"] == ["SOLV"]
    assert recon["inserted"] == 1  # only AAPL
    open_tickers = {p.ticker for p in db.get_open_positions()}
    assert "AAPL" in open_tickers
    assert "SOLV" not in open_tickers


def test_reconcile_inserts_normally_without_cooldown(db):
    alpaca = [{"ticker": "AAPL", "qty": 5, "avg_entry": 100.0, "current_price": 101.0}]
    recon = db.reconcile_positions(alpaca)  # no cooldown set passed
    assert recon["cooldown_holdings"] == []
    assert recon["inserted"] == 1
    assert {p.ticker for p in db.get_open_positions()} == {"AAPL"}


# --- Fill path: _reconcile_pending_buys --------------------------------------


def _pipeline_for_buys(cooldown_set):
    p = TradingPipeline.__new__(TradingPipeline)
    p.config = MagicMock()
    p.config.trading = {"cooldown_hours": 8}
    p.db = MagicMock()
    p.broker = MagicMock()
    p.db.get_pending_buy_orders.return_value = [
        {"id": 1, "alpaca_order_id": "o1", "ticker": "SOLV", "qty": 10}
    ]
    p.db.get_open_positions.return_value = []
    p.db.get_recently_profit_sold.return_value = cooldown_set
    p.db.get_stock_sector.return_value = "Tech"
    p.broker.get_order.return_value = {
        "status": "filled",
        "filled_price": 76.88,
        "filled_qty": 10,
    }
    return p


def test_pending_buy_fill_on_cooldown_is_flattened():
    p = _pipeline_for_buys({"SOLV"})
    p._reconcile_pending_buys()
    # Late fill of a cooled-down name is flattened, not tracked.
    p.broker.close_position.assert_called_once_with("SOLV", 10)
    p.db.save_position.assert_not_called()


def test_pending_buy_fill_off_cooldown_opens_position():
    p = _pipeline_for_buys(set())  # nothing on cooldown
    p._reconcile_pending_buys()
    p.broker.close_position.assert_not_called()
    p.db.save_position.assert_called_once()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
