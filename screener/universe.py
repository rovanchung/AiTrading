"""Stock universe management — fetches and caches S&P 500 ticker list."""

import logging
import io
from urllib.request import Request, urlopen

import pandas as pd

from core.database import Database

logger = logging.getLogger("aitrading.screener.universe")

_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_sp500_tickers() -> list[dict]:
    """Fetch S&P 500 constituents from Wikipedia."""
    logger.info("Fetching S&P 500 list from Wikipedia...")
    req = Request(_SP500_URL, headers={"User-Agent": "Mozilla/5.0 AiTrading/1.0"})
    with urlopen(req) as resp:
        html = resp.read().decode("utf-8")
    tables = pd.read_html(io.StringIO(html))
    df = tables[0]
    stocks = []
    for _, row in df.iterrows():
        ticker = row["Symbol"].replace(".", "-")  # BRK.B -> BRK-B for yfinance
        stocks.append({
            "ticker": ticker,
            "name": row.get("Security", ""),
            "sector": row.get("GICS Sector", ""),
        })
    logger.info(f"Fetched {len(stocks)} S&P 500 tickers")
    return stocks


def refresh_universe(db: Database, force: bool = False, max_age_days: float = 7.0):
    """Refresh the universe if stale or forced."""
    age = db.get_universe_age_days()
    if not force and age is not None and age < max_age_days:
        logger.info(f"Universe is {age:.1f} days old, skipping refresh")
        return

    stocks = fetch_sp500_tickers()
    for s in stocks:
        db.upsert_universe(s["ticker"], s["name"], s["sector"], 0.0)
    logger.info(f"Universe refreshed with {len(stocks)} stocks")


def get_universe_tickers(db: Database) -> list[str]:
    """Get ticker list, refreshing if needed."""
    tickers = db.get_universe_tickers()
    if not tickers:
        refresh_universe(db, force=True)
        tickers = db.get_universe_tickers()
    return tickers
