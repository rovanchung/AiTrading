#!/usr/bin/env python3
"""Sell all open positions on Alpaca and close them in the DB."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import load_config
from core.database import Database
from executor.alpaca_client import AlpacaClient

config = load_config()
db = Database(config.db_path)
broker = AlpacaClient(config)

# Cancel all open orders first
print("=== Cancelling open orders ===")
open_orders = broker.get_open_orders()
for o in open_orders:
    print(f"  Cancelling {o['side']} {o['qty']} {o['ticker']} ({o['order_id'][:8]}...)")
    broker.cancel_order(o["order_id"])
print(f"Cancelled {len(open_orders)} orders.\n")

# Sell all Alpaca positions
print("=== Selling Alpaca positions ===")
alpaca_positions = broker.get_positions()
for p in alpaca_positions:
    ticker, qty = p["ticker"], p["qty"]
    print(f"  Closing {qty} {ticker} @ ~${p['current_price']:.2f} (PnL: ${p['unrealized_pnl']:.2f})")
    try:
        result = broker.close_position(ticker, qty)
        print(f"    -> order {result['order_id'][:8]}... status={result['status']}")
    except Exception as e:
        print(f"    -> ERROR: {e}")
print(f"Submitted sell orders for {len(alpaca_positions)} positions.\n")

# Close all open positions in DB
print("=== Closing DB positions ===")
db_positions = db.get_open_positions()
for pos in db_positions:
    # Use current Alpaca price if available, else entry price
    alpaca_match = next((p for p in alpaca_positions if p["ticker"] == pos.ticker), None)
    exit_price = alpaca_match["current_price"] if alpaca_match else pos.entry_price
    db.close_position(pos.id, exit_price, "manual_sell_all")
    pnl = (exit_price - pos.entry_price) * pos.qty
    print(f"  Closed {pos.ticker} (id={pos.id}): entry=${pos.entry_price:.2f} exit=${exit_price:.2f} PnL=${pnl:.2f}")
print(f"Closed {len(db_positions)} DB positions.\n")

# Also cancel any DB orders that are still pending
pending = db.conn.execute(
    "SELECT id, ticker, side, status FROM orders WHERE status IN ('submitted', 'pending', 'new', 'accepted', 'pending_new', 'partially_filled')"
).fetchall()
for row in pending:
    db.conn.execute("UPDATE orders SET status='cancelled' WHERE id=?", (row["id"],))
    print(f"  Marked DB order {row['id']} ({row['side']} {row['ticker']}) as cancelled")
db.conn.commit()
print(f"Cancelled {len(pending)} pending DB orders.")

db.close()
print("\nDone! All positions sold and DB updated.")
