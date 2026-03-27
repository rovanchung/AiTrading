"""Full trading pipeline: scan → analyze → decide → execute."""

import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from core.config import Config
from core.database import Database
from core.models import Position
from screener.screener import StockScreener
from screener.universe import refresh_universe
from analyzer.analyzer import StockAnalyzer
from portfolio.manager import PortfolioManager
from portfolio.risk import calculate_stop_loss, calculate_take_profit
from executor.alpaca_client import AlpacaClient
from executor.order_manager import OrderManager
from analyzer.economic import MacroAnalyzer
from monitor.alerts import AlertManager

logger = logging.getLogger("aitrading.orchestrator.pipeline")
txn_logger = logging.getLogger("aitrading.transactions")


class TradingPipeline:
    """Orchestrates the complete scan-analyze-trade cycle."""

    def __init__(
        self,
        config: Config,
        db: Database,
        broker: AlpacaClient,
        order_mgr: OrderManager,
        alerts: AlertManager,
    ):
        self.config = config
        self.db = db
        self.broker = broker
        self.order_mgr = order_mgr
        self.alerts = alerts

        self.screener = StockScreener(config, db)
        self.analyzer = StockAnalyzer(config, db)
        self.portfolio_mgr = PortfolioManager(config, db)
        self.macro = MacroAnalyzer()

        # Cached shortlist: top tickers from last full scan for intra-hour re-ranking
        self._shortlist = []

        # Lock for atomic trade execution (get positions + execute)
        self._trade_lock = threading.Lock()

    def pre_market_prep(self):
        """Run at 9:25 AM: refresh universe, macro assessment, screen, and score."""
        t0 = time.time()
        logger.info("=" * 60)
        logger.info("=== PRE-MARKET PREP START ===")
        logger.info("=" * 60)

        # Universe refresh
        max_age = self.config.get("data.universe_refresh_days", 7)
        refresh_universe(self.db, max_age_days=max_age)

        # Macro assessment
        logger.info("--- Macro assessment ---")
        macro = self.macro.get_macro_assessment()
        self.portfolio_mgr.set_macro_adjustments(macro.get("adjustments"))
        self.alerts.macro_update(macro)

        # Screen for candidates
        logger.info("--- Screening ---")
        candidates = self.screener.scan()
        if not candidates:
            logger.warning("No candidates found in scan")
            logger.info(f"Pre-market prep complete, no candidates ({time.time() - t0:.1f}s)")
            return

        # Fetch data and score
        logger.info("--- Fetching data ---")
        data = self.screener.get_data_for_tickers(candidates)
        spy_df = self.screener._fetch_single("SPY")

        logger.info("--- Analyzing ---")
        scored = self.analyzer.analyze_batch(candidates, data, spy_df)
        if not scored:
            logger.warning("No stocks scored successfully")
            logger.info(f"Pre-market prep complete, no scores ({time.time() - t0:.1f}s)")
            return

        self._update_shortlist(scored)
        logger.info(
            f"=== PRE-MARKET PREP COMPLETE — {len(scored)} stocks scored, "
            f"shortlist={len(self._shortlist)} ({time.time() - t0:.1f}s) ==="
        )

    def run_full_cycle(self, deadline_minutes=12):
        """Run the complete scan → analyze → trade pipeline with retry until deadline.

        Args:
            deadline_minutes: Max minutes to retry if cycle fails (default 12, for XX:28 to XX:40).
        """
        t0 = time.time()
        deadline = t0 + deadline_minutes * 60
        logger.info("=" * 60)
        logger.info("=== FULL TRADING CYCLE START ===")
        logger.info("=" * 60)

        attempt = 0
        while True:
            attempt += 1
            try:
                # Check market is open
                if not self.broker.is_market_open():
                    logger.info("Market is closed, skipping cycle")
                    return

                # Step 1: Screen for candidates
                logger.info("--- Step 1: Screening ---")
                candidates = self.screener.scan()
                if not candidates:
                    logger.warning("No candidates found in scan")
                    return

                # Step 2: Fetch detailed data for candidates
                logger.info("--- Step 2: Fetching data ---")
                data = self.screener.get_data_for_tickers(candidates)
                spy_df = self.screener._fetch_single("SPY")

                # Step 3: Analyze and score candidates
                logger.info("--- Step 3: Analyzing ---")
                scored = self.analyzer.analyze_batch(candidates, data, spy_df)
                if not scored:
                    logger.warning("No stocks scored successfully")
                    return

                # Step 4: Macro assessment (if cache expired)
                if not self.macro._is_cache_valid():
                    logger.info("--- Step 3b: Macro assessment ---")
                    macro = self.macro.get_macro_assessment()
                    self.portfolio_mgr.set_macro_adjustments(macro.get("adjustments"))

                # Cache shortlist for intra-hour re-ranking
                self._update_shortlist(scored)

                # Step 5: Atomic evaluate + execute
                logger.info("--- Step 4: Evaluating & executing ---")
                self._atomic_evaluate_and_execute(scored, data)

                logger.info(f"=== FULL TRADING CYCLE COMPLETE ({time.time() - t0:.1f}s) ===")
                return

            except Exception as e:
                now = time.time()
                if now >= deadline:
                    logger.error(f"Full cycle failed after {attempt} attempts, deadline reached: {e}")
                    self.alerts.order_failed("CYCLE", str(e))
                    return
                wait = min(30, deadline - now)
                logger.warning(f"Full cycle attempt {attempt} failed: {e}, retrying in {wait:.0f}s")
                time.sleep(wait)

    def _update_shortlist(self, scored):
        """Cache the top candidates + held tickers as the shortlist for re-ranking."""
        shortlist_size = self.config.get("schedule.shortlist_size", 50)
        top_tickers = [s.ticker for s in scored[:shortlist_size]]

        # Always include currently held tickers
        positions = self.db.get_open_positions()
        held_tickers = {p.ticker for p in positions}
        shortlist = list(dict.fromkeys(top_tickers + list(held_tickers)))

        self._shortlist = shortlist
        logger.info(f"Shortlist updated: {len(shortlist)} tickers ({len(held_tickers)} held + top {shortlist_size})")

    def run_rerank_cycle(self):
        """Re-rank the cached shortlist and rebalance portfolio.

        Uses the shortlist from the last full scan — much faster than a full cycle
        since it only fetches ~50 tickers instead of ~500.

        If called before market open (e.g. 9:29:50), analysis runs immediately
        and execution is deferred to market open via a timer.
        """
        if not self._shortlist:
            if self.broker.is_market_open():
                logger.warning("No shortlist cached, running full cycle instead")
                self.run_full_cycle()
            return

        t0 = time.time()
        logger.info(f"=== RE-RANK CYCLE START ({len(self._shortlist)} tickers) ===")

        try:
            # Re-fetch data and re-score only the shortlist
            data = self.screener.get_data_for_tickers(self._shortlist)
            spy_df = self.screener._fetch_single("SPY")
            scored = self.analyzer.analyze_batch(self._shortlist, data, spy_df)

            if not scored:
                logger.warning("No stocks scored in re-rank")
                return

            # Execute immediately if market is open, otherwise defer to market open
            if self.broker.is_market_open():
                self._atomic_evaluate_and_execute(scored, data)
            else:
                self._defer_to_market_open(scored, data)

            logger.info(f"=== RE-RANK CYCLE COMPLETE ({time.time() - t0:.1f}s) ===")

        except Exception as e:
            logger.error(f"Re-rank cycle failed: {e}")

    def _defer_to_market_open(self, scored, data: dict):
        """Schedule trade execution at market open using a timer."""
        open_str = self.config.schedule.get("market_open", "09:30")
        h, m = (int(x) for x in open_str.split(":"))

        now = datetime.now(ZoneInfo("US/Eastern"))
        market_open = now.replace(hour=h, minute=m, second=0, microsecond=0)
        delay = (market_open - now).total_seconds()

        if delay <= 0:
            # Market should already be open, execute now
            self._atomic_evaluate_and_execute(scored, data)
            return

        logger.info(f"Market opens in {delay:.0f}s, deferring execution to {open_str} ET")
        timer = threading.Timer(delay, self._atomic_evaluate_and_execute, args=[scored, data])
        timer.daemon = True
        timer.start()

    def _atomic_evaluate_and_execute(self, scored, data: dict):
        """Atomically get positions, evaluate signals, and execute trades.

        Holds the trade lock to prevent concurrent position reads/writes
        from the position monitor or rescore jobs.
        """
        with self._trade_lock:
            account = self.broker.get_account()
            positions = self.db.get_open_positions()

            # Save portfolio snapshot
            peak = max(self.db.get_peak_value(), account["portfolio_value"])
            self.db.save_portfolio_snapshot(
                account["portfolio_value"], account["cash"],
                account["portfolio_value"] - account["cash"], peak
            )

            # Generate signals
            signals = self.portfolio_mgr.evaluate(scored, positions, account, data)
            if not signals:
                logger.info("No trading signals generated")
                return

            # Execute signals
            logger.info(f"Executing {len(signals)} signals (lock held)")
            self._execute_signals(signals, data, positions)

    def _execute_signals(
        self, signals, data: dict, positions: list[Position]
    ):
        """Execute a list of buy/sell signals. Caller must hold _trade_lock."""
        pos_map = {p.ticker: p for p in positions}

        for signal in signals:
            try:
                df = data.get(signal.ticker)
                current_price = df["Close"].iloc[-1] if df is not None and not df.empty else 0

                if current_price <= 0:
                    logger.warning(f"No price data for {signal.ticker}, skipping")
                    continue

                order = self.order_mgr.execute_signal(signal, current_price)

                if order.status == "failed":
                    self.alerts.order_failed(signal.ticker, order.error_message)
                    continue

                fill_price = order.filled_price or current_price

                if signal.action == "buy":
                    # Create position record
                    pos = Position(
                        ticker=signal.ticker,
                        qty=signal.suggested_qty,
                        entry_price=fill_price,
                        entry_time=datetime.now(),
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        high_water_mark=fill_price,
                        status="open",
                        sector=self.db.get_stock_sector(signal.ticker),
                    )
                    self.db.save_position(pos)
                    txn_logger.info(
                        f"BUY  | {signal.ticker} | qty={signal.suggested_qty} | "
                        f"price={fill_price:.2f} | stop={signal.stop_loss:.2f} | "
                        f"tp={signal.take_profit:.2f} | sector={pos.sector}"
                    )
                    self.alerts.position_opened(signal.ticker, signal.suggested_qty, fill_price)

                elif signal.action == "sell":
                    existing = pos_map.get(signal.ticker)
                    if existing:
                        self.db.close_position(existing.id, fill_price, signal.reason)
                        pnl = (fill_price - existing.entry_price) * existing.qty
                        pnl_pct = ((fill_price - existing.entry_price) / existing.entry_price) * 100
                        txn_logger.info(
                            f"SELL | {signal.ticker} | qty={existing.qty} | "
                            f"entry={existing.entry_price:.2f} | exit={fill_price:.2f} | "
                            f"pnl=${pnl:.2f} ({pnl_pct:+.1f}%) | reason={signal.reason}"
                        )
                        self.alerts.position_closed(
                            signal.ticker, signal.suggested_qty, fill_price,
                            signal.reason, pnl,
                        )

            except Exception as e:
                logger.error(f"Failed to execute signal for {signal.ticker}: {e}")
                self.alerts.order_failed(signal.ticker, str(e))
