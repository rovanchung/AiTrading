"""Backfill entry_score / exit_score on existing positions.

For each position with a NULL entry_score, take the most recent
score row for the same ticker with scored_at <= entry_time.
Same for exit_score / exit_time. Run once per DB.
"""

import sqlite3
import sys
from pathlib import Path

BACKFILL_ENTRY = """
UPDATE positions
SET entry_score = (
    SELECT s.composite_score
    FROM scores s
    WHERE s.ticker = positions.ticker
      AND s.scored_at <= positions.entry_time
    ORDER BY s.scored_at DESC
    LIMIT 1
)
WHERE entry_score IS NULL AND entry_time IS NOT NULL;
"""

BACKFILL_EXIT = """
UPDATE positions
SET exit_score = (
    SELECT s.composite_score
    FROM scores s
    WHERE s.ticker = positions.ticker
      AND s.scored_at <= positions.exit_time
    ORDER BY s.scored_at DESC
    LIMIT 1
)
WHERE status = 'closed' AND exit_score IS NULL AND exit_time IS NOT NULL;
"""


def backfill(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(BACKFILL_ENTRY)
        e = cur.rowcount
        cur = conn.execute(BACKFILL_EXIT)
        x = cur.rowcount
        conn.commit()
        print(f"  {db_path}: entry={e} exit={x}")
    finally:
        conn.close()


if __name__ == "__main__":
    paths = sys.argv[1:] or [str(p) for p in Path("data").glob("trading*.db")]
    for p in paths:
        backfill(p)
