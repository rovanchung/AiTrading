"""Tests for cash-guard logic in orchestrator/pipeline.py."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.models import Signal
from orchestrator.pipeline import TradingPipeline


def _pipeline(cash_slippage=0.005):
    """Minimal TradingPipeline that only supports _apply_cash_guard."""
    p = TradingPipeline.__new__(TradingPipeline)
    p.config = MagicMock()
    p.config.trading = {
        "cash_only": True,
        "cash_slippage_buffer": cash_slippage,
    }
    return p


def _bar(close: float) -> pd.DataFrame:
    return pd.DataFrame({"Close": [close]})


def test_no_scaling_when_cash_covers_buys():
    p = _pipeline()
    buys = [Signal(ticker="AAPL", action="buy", suggested_qty=10)]
    data = {"AAPL": _bar(100.0)}
    result = p._apply_cash_guard([], buys, data, {}, cash_available=5000)
    assert len(result) == 1
    assert result[0].suggested_qty == 10  # unchanged


def test_scaling_when_buys_exceed_cash():
    p = _pipeline(cash_slippage=0.0)
    buys = [
        Signal(ticker="AAPL", action="buy", suggested_qty=100),  # $10k
        Signal(ticker="GOOG", action="buy", suggested_qty=100),  # $10k
    ]
    data = {"AAPL": _bar(100.0), "GOOG": _bar(100.0)}
    result = p._apply_cash_guard([], buys, data, {}, cash_available=5000)
    # Budget $5k / total $20k = 0.25 scale → 25 each
    assert len(result) == 2
    assert result[0].suggested_qty == 25
    assert result[1].suggested_qty == 25


def test_zero_cash_drops_all_buys():
    p = _pipeline()
    buys = [Signal(ticker="AAPL", action="buy", suggested_qty=10)]
    data = {"AAPL": _bar(100.0)}
    result = p._apply_cash_guard([], buys, data, {}, cash_available=0)
    assert result == []


def test_negative_cash_drops_all_buys():
    p = _pipeline()
    buys = [Signal(ticker="AAPL", action="buy", suggested_qty=10)]
    data = {"AAPL": _bar(100.0)}
    result = p._apply_cash_guard([], buys, data, {}, cash_available=-500)
    assert result == []


def test_sell_proceeds_expand_budget():
    p = _pipeline(cash_slippage=0.0)
    sells = [Signal(ticker="TSLA", action="sell", suggested_qty=100)]  # $15k proceeds
    buys = [Signal(ticker="AAPL", action="buy", suggested_qty=100)]    # $10k cost
    data = {"TSLA": _bar(150.0), "AAPL": _bar(100.0)}
    # Cash $2k + sells $15k = $17k budget, covers $10k buy — no scaling
    result = p._apply_cash_guard(sells, buys, data, {}, cash_available=2000)
    assert len(result) == 1
    assert result[0].suggested_qty == 100


def test_pending_buy_reserves_cash():
    p = _pipeline(cash_slippage=0.0)
    buys = [Signal(ticker="AAPL", action="buy", suggested_qty=100)]  # $10k
    pending = {
        "MSFT": [
            {"side": "OrderSide.BUY", "qty": 50, "order_id": "x"},
        ]
    }
    data = {"AAPL": _bar(100.0), "MSFT": _bar(200.0)}  # MSFT pending = $10k
    # Cash $15k - pending $10k = $5k budget → half of AAPL buy
    result = p._apply_cash_guard([], buys, data, pending, cash_available=15000)
    assert len(result) == 1
    assert result[0].suggested_qty == 50


def test_slippage_buffer_applied():
    p = _pipeline(cash_slippage=0.10)  # 10% buffer for visibility
    buys = [Signal(ticker="AAPL", action="buy", suggested_qty=10)]
    data = {"AAPL": _bar(100.0)}
    # Actual cost with 10% slippage = 10 × 100 × 1.10 = $1,100
    # Cash $1,050 < cost $1,100 → scale to 1050/1100 = 0.9545 → 9
    result = p._apply_cash_guard([], buys, data, {}, cash_available=1050)
    assert len(result) == 1
    assert result[0].suggested_qty == 9


def test_buy_with_missing_price_dropped():
    p = _pipeline()
    buys = [
        Signal(ticker="AAPL", action="buy", suggested_qty=10),  # has data
        Signal(ticker="XXX", action="buy", suggested_qty=10),   # no data
    ]
    data = {"AAPL": _bar(100.0)}  # XXX missing
    result = p._apply_cash_guard([], buys, data, {}, cash_available=5000)
    # Only AAPL survives; no scaling needed
    assert len(result) == 1
    assert result[0].ticker == "AAPL"


def test_empty_buy_list_returns_empty():
    p = _pipeline()
    result = p._apply_cash_guard([], [], {}, {}, cash_available=1000)
    assert result == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
