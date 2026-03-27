#!/usr/bin/env python3
"""Manual scan — run a one-off screening and analysis for debugging.

Usage:
    python scripts/manual_scan.py                    # Screen + show candidates
    python scripts/manual_scan.py --analyze          # Screen + full analysis
    python scripts/manual_scan.py --tickers AAPL MSFT GOOGL  # Analyze specific tickers
    python scripts/manual_scan.py --top 10           # Show top N results
"""

import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import load_config
from core.database import Database
from core.logging_config import setup_logging
from screener.screener import StockScreener
from analyzer.analyzer import StockAnalyzer


def main():
    parser = argparse.ArgumentParser(description="Manual stock scan")
    parser.add_argument("--analyze", action="store_true", help="Run full analysis on candidates")
    parser.add_argument("--tickers", nargs="+", help="Analyze specific tickers")
    parser.add_argument("--top", type=int, default=20, help="Show top N results")
    args = parser.parse_args()

    config = load_config()
    logger = setup_logging(config)
    db = Database(config.db_path)
    db.init_schema()

    screener = StockScreener(config, db)

    if args.tickers:
        # Analyze specific tickers
        tickers = [t.upper() for t in args.tickers]
        print(f"\nFetching data for: {', '.join(tickers)}")
        data = screener.get_data_for_tickers(tickers)
        spy_df = screener._fetch_single("SPY")

        analyzer = StockAnalyzer(config, db)
        scored = analyzer.analyze_batch(tickers, data, spy_df)

        print(f"\n{'='*80}")
        print(f"{'Ticker':8s} {'Composite':>10s} {'Technical':>10s} {'Fundament':>10s} {'Momentum':>10s} {'Sentiment':>10s}")
        print(f"{'='*80}")
        for s in scored:
            print(
                f"{s.ticker:8s} {s.composite:10.1f} {s.technical:10.1f} "
                f"{s.fundamental:10.1f} {s.momentum:10.1f} {s.sentiment:10.1f}"
            )

        # Show detailed breakdown for each
        for s in scored:
            print(f"\n--- {s.ticker} Details ---")
            for dim, details in s.details.items():
                print(f"  {dim}:")
                if isinstance(details, dict):
                    for k, v in details.items():
                        print(f"    {k}: {v}")
    else:
        # Full scan
        print("\nRunning stock screener...")
        candidates = screener.scan()

        if not candidates:
            print("No candidates found!")
            db.close()
            return

        print(f"\n{len(candidates)} candidates passed all filters:")
        for i, ticker in enumerate(candidates, 1):
            print(f"  {i:3d}. {ticker}")

        if args.analyze:
            print(f"\nRunning full analysis on {len(candidates)} candidates...")
            data = screener.get_data_for_tickers(candidates)
            spy_df = screener._fetch_single("SPY")

            analyzer = StockAnalyzer(config, db)
            scored = analyzer.analyze_batch(candidates, data, spy_df)

            print(f"\n{'='*80}")
            print(f"{'#':>3s} {'Ticker':8s} {'Composite':>10s} {'Technical':>10s} {'Fundament':>10s} {'Momentum':>10s} {'Sentiment':>10s}")
            print(f"{'='*80}")
            for i, s in enumerate(scored[:args.top], 1):
                print(
                    f"{i:3d} {s.ticker:8s} {s.composite:10.1f} {s.technical:10.1f} "
                    f"{s.fundamental:10.1f} {s.momentum:10.1f} {s.sentiment:10.1f}"
                )

            if scored:
                top = scored[0]
                print(f"\n=== Top Pick: {top.ticker} (Score: {top.composite:.1f}) ===")
                for dim, details in top.details.items():
                    print(f"  {dim}:")
                    if isinstance(details, dict):
                        for k, v in details.items():
                            print(f"    {k}: {v}")

    db.close()


if __name__ == "__main__":
    main()
