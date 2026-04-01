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
        self.macro_enabled = config.get("macro.enabled", True)
        self.macro = MacroAnalyzer() if self.macro_enabled else None

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
        if self.macro:
            logger.info("--- Macro assessment ---")
            macro = self.macro.get_macro_assessment()
            self.portfolio_mgr.set_macro_adjustments(macro.get("adjustments"))
            self.alerts.macro_update(macro)
        else:
            logger.info("--- Macro overlay disabled ---")

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
                if self.macro and not self.macro._is_cache_valid():
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
        if not self.broker.is_market_open():
            return

        if not self._shortlist:
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

            self._atomic_evaluate_and_execute(scored, data)

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

    def _sync_pending_orders(self) -> dict[str, list[dict]]:
        """Sync pending orders: reconcile fills/cancels and return open orders.

        For buy orders that were submitted but not immediately filled:
        - If now filled on Alpaca: create the position in DB
        - If canceled/expired on Alpaca: mark order as canceled in DB
        Also returns the map of currently open orders on Alpaca.
        """
        # 1. Reconcile DB pending buy orders against Alpaca
        self._reconcile_pending_buys()

        # 2. Get currently open orders from Alpaca
        try:
            open_orders = self.broker.get_open_orders()
        except Exception as e:
            logger.error(f"Failed to fetch open orders: {e}")
            return {}

        by_ticker: dict[str, list[dict]] = {}
        for o in open_orders:
            by_ticker.setdefault(o["ticker"], []).append(o)

        if open_orders:
            tickers = {o["ticker"] for o in open_orders}
            logger.info(f"Pending orders: {len(open_orders)} for {tickers}")

        return by_ticker

    def _reconcile_pending_buys(self):
        """Check DB pending buy orders against Alpaca and reconcile."""
        pending = self.db.get_pending_buy_orders()
        if not pending:
            return

        # Build set of tickers that already have open DB positions
        open_positions = self.db.get_open_positions()
        open_tickers = {p.ticker for p in open_positions}

        for db_order in pending:
            try:
                result = self.broker.get_order(db_order["alpaca_order_id"])
                alpaca_status = result["status"].lower().replace("orderstatus.", "")

                if alpaca_status == "filled":
                    fill_price = result["filled_price"]
                    self.db.update_order(
                        db_order["id"],
                        status="filled",
                        filled_price=fill_price,
                        filled_at=datetime.now(),
                    )
                    # Create position if one doesn't already exist
                    if db_order["ticker"] not in open_tickers:
                        qty = result.get("filled_qty") or db_order["qty"]
                        pos = Position(
                            ticker=db_order["ticker"],
                            qty=qty,
                            entry_price=fill_price,
                            entry_time=datetime.now(),
                            high_water_mark=fill_price,
                            status="open",
                            sector=self.db.get_stock_sector(db_order["ticker"]),
                        )
                        self.db.save_position(pos)
                        txn_logger.info(
                            f"BUY  | {db_order['ticker']} | qty={qty} | "
                            f"price={fill_price:.2f} | (reconciled from pending order)"
                        )
                        open_tickers.add(db_order["ticker"])
                    logger.info(
                        f"Reconciled filled buy for {db_order['ticker']} "
                        f"@ {fill_price}"
                    )

                elif alpaca_status in ("canceled", "cancelled", "expired", "rejected"):
                    self.db.update_order(
                        db_order["id"], status="canceled"
                    )
                    logger.info(
                        f"Buy order for {db_order['ticker']} was {alpaca_status}, "
                        f"updated DB"
                    )

                # else: still pending, leave as-is

            except Exception as e:
                logger.warning(
                    f"Failed to reconcile order {db_order['alpaca_order_id']} "
                    f"for {db_order['ticker']}: {e}"
                )

    def _cancel_pending_orders_for(self, ticker: str, side: str,
                                    pending_orders: dict[str, list[dict]]):
        """Cancel all pending orders for a ticker on a given side."""
        for order in pending_orders.get(ticker, []):
            if order["side"].lower().replace("ordersid.", "").replace("orderside.", "") == side or side == "all":
                try:
                    self.broker.cancel_order(order["order_id"])
                    logger.info(f"Cancelled pending {order['side']} for {ticker}")
                except Exception as e:
                    logger.warning(f"Failed to cancel order {order['order_id']}: {e}")

    def _atomic_evaluate_and_execute(self, scored, data: dict):
        """Atomically get positions, evaluate signals, and execute trades.

        Holds the trade lock to prevent concurrent position reads/writes
        from the position monitor or rescore jobs.
        """
        with self._trade_lock:
            # Sync pending orders from Alpaca before making decisions
            pending_orders = self._sync_pending_orders()
            pending_buy_tickers = {
                t for t, orders in pending_orders.items()
                if any("buy" in o["side"].lower() for o in orders)
            }

            account = self.broker.get_account()
            positions = self.db.get_open_positions()
            alpaca_positions = self.broker.get_positions()

            # Save portfolio snapshot
            peak = max(self.db.get_peak_value(), account["portfolio_value"])
            self.db.save_portfolio_snapshot(
                account["portfolio_value"], account["cash"],
                account["portfolio_value"] - account["cash"], peak
            )

            # Generate signals
            signals = self.portfolio_mgr.evaluate(
                scored, positions, account, alpaca_positions, data
            )
            if not signals:
                logger.info("No trading signals generated")
                return

            # Filter out buy signals for tickers with pending buy orders,
            # and sell signals for tickers that only have a pending buy (no filled position).
            filtered = []
            for s in signals:
                if s.action == "buy" and s.ticker in pending_buy_tickers:
                    logger.info(f"Skipping buy {s.ticker}: pending buy order exists")
                    continue
                if s.action == "sell" and s.ticker in pending_buy_tickers:
                    logger.info(f"Skipping sell {s.ticker}: only has pending buy, no filled position")
                    continue
                filtered.append(s)

            if not filtered:
                logger.info("No signals after filtering pending orders")
                return

            # Execute signals
            logger.info(f"Executing {len(filtered)} signals (lock held)")
            self._execute_signals(filtered, data, positions, pending_orders)

    def _execute_signals(
        self, signals, data: dict, positions: list[Position],
        pending_orders: dict[str, list[dict]] = None,
    ):
        """Execute a list of buy/sell signals. Caller must hold _trade_lock."""
        pos_map = {p.ticker: p for p in positions}
        pending_orders = pending_orders or {}

        for signal in signals:
            try:
                df = data.get(signal.ticker)
                current_price = df["Close"].iloc[-1] if df is not None and not df.empty else 0

                if current_price <= 0:
                    logger.warning(f"No price data for {signal.ticker}, skipping")
                    continue

                # Cancel any pending buy orders before selling to avoid
                # the buy filling after we've sold the position
                if signal.action == "sell":
                    self._cancel_pending_orders_for(signal.ticker, "buy", pending_orders)

                order = self.order_mgr.execute_signal(signal, current_price)

                if order.status == "failed":
                    self.alerts.order_failed(signal.ticker, order.error_message)
                    # If sell failed because position doesn't exist on Alpaca,
                    # close the stale DB record so we stop retrying
                    if (
                        signal.action == "sell"
                        and order.error_message
                        and "no open position" in order.error_message
                    ):
                        existing = pos_map.get(signal.ticker)
                        if existing:
                            logger.warning(
                                f"Closing stale DB position for {signal.ticker} "
                                f"(not found on Alpaca)"
                            )
                            self.db.close_position(
                                existing.id, existing.entry_price, "stale_position_cleanup"
                            )
                    continue

                fill_price = order.filled_price or current_price

                if signal.action == "buy":
                    if order.status != "filled":
                        logger.info(
                            f"Buy order for {signal.ticker} accepted "
                            f"(status={order.status}), awaiting fill"
                        )
                    else:
                        pos = Position(
                            ticker=signal.ticker,
                            qty=order.qty,
                            entry_price=fill_price,
                            entry_time=datetime.now(),
                            high_water_mark=fill_price,
                            status="open",
                            sector=self.db.get_stock_sector(signal.ticker),
                        )
                        self.db.save_position(pos)
                        txn_logger.info(
                            f"BUY  | {signal.ticker} | qty={order.qty} | "
                            f"price={fill_price:.2f} | reason={signal.reason}"
                        )
                        self.alerts.position_opened(signal.ticker, order.qty, fill_price)

                elif signal.action == "sell":
                    existing = pos_map.get(signal.ticker)
                    if existing:
                        sold_qty = min(signal.suggested_qty, existing.qty)
                        if sold_qty >= existing.qty:
                            # Full sell — close DB position
                            self.db.close_position(existing.id, fill_price, signal.reason)
                        else:
                            # Partial sell — reduce qty in DB
                            self.db.update_position(
                                existing.id, qty=existing.qty - sold_qty
                            )
                        pnl = (fill_price - existing.entry_price) * sold_qty
                        pnl_pct = ((fill_price - existing.entry_price) / existing.entry_price) * 100
                        txn_logger.info(
                            f"SELL | {signal.ticker} | qty={sold_qty} | "
                            f"entry={existing.entry_price:.2f} | exit={fill_price:.2f} | "
                            f"pnl=${pnl:.2f} ({pnl_pct:+.1f}%) | reason={signal.reason}"
                        )
                        self.alerts.position_closed(
                            signal.ticker, sold_qty, fill_price,
                            signal.reason, pnl,
                        )

            except Exception as e:
                logger.error(f"Failed to execute signal for {signal.ticker}: {e}")
                self.alerts.order_failed(signal.ticker, str(e))
