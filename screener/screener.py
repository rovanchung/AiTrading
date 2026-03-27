"""Main stock screener — chains filters to find candidates."""

import logging
from typing import Optional

import pandas as pd

from core.config import Config
from core.yf_helpers import yf_download
from core.database import Database
from screener.universe import get_universe_tickers
from screener.filters import filter_volume, filter_moving_average, filter_relative_strength

logger = logging.getLogger("aitrading.screener")


class StockScreener:
    """Screens the stock universe to find growth candidates."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.sc = config.screener

    def scan(self) -> list[str]:
        """Run the full screening pipeline. Returns list of candidate tickers."""
        tickers = get_universe_tickers(self.db)
        logger.info(f"Starting scan on {len(tickers)} tickers...")

        # Batch download OHLCV data
        data = self._fetch_data(tickers)
        if not data:
            logger.warning("No data fetched, aborting scan")
            return []

        # Fetch SPY for relative strength comparison
        spy_df = self._fetch_single("SPY")

        # Apply filters sequentially, narrowing the candidate list
        candidates = list(data.keys())
        logger.info(f"After data fetch: {len(candidates)} tickers with valid data")

        # Price filter
        candidates = [t for t in candidates if self._passes_price_filter(data[t])]
        logger.info(f"After price filter: {len(candidates)}")

        # Volume filter
        vol_passed = filter_volume(
            {t: data[t] for t in candidates},
            min_avg_volume=int(self.sc.get("min_avg_volume", 500_000)),
        )
        candidates = [t for t in candidates if t in vol_passed]
        logger.info(f"After volume filter: {len(candidates)}")

        # Moving average filter (uptrend)
        ma_passed = filter_moving_average({t: data[t] for t in candidates})
        candidates = [t for t in candidates if t in ma_passed]
        logger.info(f"After MA filter: {len(candidates)}")

        # Relative strength filter
        rs_passed = filter_relative_strength(
            {t: data[t] for t in candidates}, spy_df
        )
        candidates = [t for t in candidates if t in rs_passed]
        logger.info(f"After RS filter: {len(candidates)} candidates")

        return candidates

    def get_data_for_tickers(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """Fetch OHLCV data for a list of tickers."""
        return self._fetch_data(tickers)

    def _fetch_data(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """Batch download OHLCV data using yfinance."""
        period = self.config.get("data.history_period", "3mo")
        raw = yf_download(tickers, period=period, group_by="ticker", threads=True, timeout=30)
        if raw.empty:
            return {}

        data = {}
        for ticker in tickers:
            try:
                if len(tickers) == 1:
                    df = raw
                    if isinstance(df.columns, pd.MultiIndex):
                        # group_by='ticker' puts ticker as level 0: ('NFLX', 'Close')
                        df.columns = df.columns.droplevel(0)
                else:
                    if ticker in raw.columns.get_level_values(0):
                        df = raw[ticker].dropna(how="all")
                    else:
                        df = pd.DataFrame()
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.droplevel(1)
                if not df.empty and len(df) >= 20:
                    data[ticker] = df
            except (KeyError, TypeError):
                continue

        logger.info(f"Fetched data for {len(data)}/{len(tickers)} tickers")
        return data

    def _fetch_single(self, ticker: str) -> pd.DataFrame:
        """Fetch data for a single ticker."""
        period = self.config.get("data.history_period", "3mo")
        df = yf_download(ticker, period=period)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        return df

    def _passes_price_filter(self, df: pd.DataFrame) -> bool:
        """Check if latest close is within configured price range."""
        if df.empty:
            return False
        price = df["Close"].iloc[-1]
        return self.sc.get("min_price", 5.0) <= price <= self.sc.get("max_price", 500.0)
