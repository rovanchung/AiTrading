"""Backfill exit_price / pnl on closed positions from the real sell fill.

Full sells close the DB position at *submit* time using the stale OHLCV bar
close, because the market order has not filled yet. The true fill price only
lands in the `orders` table 1-6 min later (via the pending-sell reconcile) and
was never propagated back to the position. This corrects historical rows by
re-linking each closed position to the sell order that closed it (matched by
ticker + exit_time ≈ order submitted_at) and rewriting exit_price/pnl.

Idempotent — safe to re-run. Run once per DB after deploying the live fix.

Usage:
    python -m scripts.backfill_exit_prices [DB_PATH ...] [--dry-run] [--tolerance SECS]

With no DB_PATH args, processes every data/trading*.db.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import Database

# Synthetic closes that never had a real sell fill — leave them alone.
SYNTHETIC_REASONS = ("reconcile_orphan", "stale_position_cleanup", "synced_external")


def _parse(ts):
    try:
        return datetime.fromisoformat(ts) if ts else None
    except (ValueError, TypeError):
        return None


def backfill(db_path: str, tolerance: float, dry_run: bool):
    db = Database(db_path)
    try:
        # Iterate the real sell fills and claim each one's closed position,
        # mirroring the live reconcile (one order -> one position). Processing
        # orders rather than positions keeps the assignment one-to-one, so a
        # phantom double-close can't pull the same fill onto two rows.
        orders = db.conn.execute(
            """SELECT ticker, qty, filled_price, submitted_at FROM orders
               WHERE side = 'sell' AND status = 'filled'
                 AND filled_price IS NOT NULL AND submitted_at IS NOT NULL
               ORDER BY submitted_at"""
        ).fetchall()

        corrected = 0
        claimed: set[int] = set()
        for o in orders:
            ref = _parse(o["submitted_at"])
            if ref is None:
                continue

            cands = db.conn.execute(
                """SELECT id, entry_price, qty, exit_price, exit_time, exit_reason
                   FROM positions
                   WHERE ticker = ? AND qty = ? AND status = 'closed'
                     AND exit_price IS NOT NULL AND exit_time IS NOT NULL""",
                (o["ticker"], o["qty"]),
            ).fetchall()

            best, best_delta = None, None
            for p in cands:
                if p["id"] in claimed or p["exit_reason"] in SYNTHETIC_REASONS:
                    continue
                et = _parse(p["exit_time"])
                if et is None:
                    continue
                delta = abs((et - ref).total_seconds())
                if delta <= tolerance and (best_delta is None or delta < best_delta):
                    best, best_delta = p, delta

            if best is None:
                continue
            claimed.add(best["id"])
            fill = o["filled_price"]
            if abs(best["exit_price"] - fill) < 1e-6:
                continue

            old_pnl = (best["exit_price"] - best["entry_price"]) * best["qty"]
            new_pnl = (fill - best["entry_price"]) * best["qty"]
            print(
                f"  {o['ticker']:6} pos {best['id']:>4}  "
                f"exit {best['exit_price']:.4f} -> {fill:.4f}  "
                f"pnl {old_pnl:+.2f} -> {new_pnl:+.2f}  ({best['exit_reason']})"
            )
            if not dry_run:
                db.correct_exit_price(best["id"], fill)
            corrected += 1

        verb = "Would correct" if dry_run else "Corrected"
        print(f"{db_path}: {verb} {corrected} positions from real sell fills")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db_paths", nargs="*", help="DB paths (default: data/trading*.db)")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument(
        "--tolerance",
        type=float,
        default=30,
        help="max seconds between exit_time and order submitted_at (default 30)",
    )
    args = ap.parse_args()

    paths = args.db_paths or [str(p) for p in sorted(Path("data").glob("trading*.db"))]
    if not paths:
        print("No databases found.")
        sys.exit(1)
    for path in paths:
        backfill(path, args.tolerance, args.dry_run)
