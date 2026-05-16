"""Full trading pipeline: scan → analyze → decide → execute."""

import logging
import threading
import time
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from core.config import Config
from core.database import Database
from core import scan_cache
from core.models import Position, Signal
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

    def _scan_or_load_cache(self, scan_max_age: float = 90):
        """Scan with lock, or load from cache if another process scanned recently.

        Returns (scored, data) or (None, None).
        """
        # If cache is fresh, skip scanning entirely
        if scan_cache.is_fresh(scan_max_age):
            logger.info("Fresh scan cache found, loading...")
            scored, data = scan_cache.load()
            if scored:
                shortlist = scan_cache.load_shortlist()
                if shortlist:
                    self._shortlist = shortlist
                return scored, data

        # Try to acquire the scan lock
        lock_fd = scan_cache.acquire_scan_lock(timeout=5.0)

        if lock_fd is None:
            # Another process is scanning — wait for it to finish
            logger.info("Another process is scanning, waiting for cache...")
            return self._wait_for_cache(scan_max_age, timeout=300)

        # We hold the lock — re-check cache (another process may have
        # finished between our freshness check and acquiring the lock)
        if scan_cache.is_fresh(scan_max_age):
            scan_cache.release_scan_lock(lock_fd)
            scored, data = scan_cache.load()
            if scored:
                shortlist = scan_cache.load_shortlist()
                if shortlist:
                    self._shortlist = shortlist
                return scored, data

        # Actually scan
        try:
            logger.info("--- Screening ---")
            candidates = self.screener.scan()
            if not candidates:
                logger.warning("No candidates found in scan")
                return None, None

            logger.info("--- Fetching data ---")
            data = self.screener.get_data_for_tickers(candidates)
            spy_df = self.screener._fetch_single("SPY")

            logger.info("--- Analyzing ---")
            scored = self.analyzer.analyze_batch(candidates, data, spy_df)
            if not scored:
                logger.warning("No stocks scored successfully")
                return None, None

            self._update_shortlist(scored)
            scan_cache.save(scored, data, self._shortlist)
            return scored, data

        finally:
            scan_cache.release_scan_lock(lock_fd)

    def _wait_for_cache(self, max_age: float, timeout: float = 300):
        """Poll until the cache is fresh or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if scan_cache.is_fresh(max_age):
                scored, data = scan_cache.load()
                if scored:
                    shortlist = scan_cache.load_shortlist()
                    if shortlist:
                        self._shortlist = shortlist
                    return scored, data
            time.sleep(2)
        logger.warning("Timed out waiting for scan cache")
        return None, None

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

        # Scan (or load from cache if another version just scanned)
        scored, data = self._scan_or_load_cache(scan_max_age=90)
        if scored is None:
            logger.info(
                f"Pre-market prep complete, no candidates ({time.time() - t0:.1f}s)"
            )
            return

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
                    logger.info("Market is closed, skipping full cycle")
                    return

                # Macro assessment (if cache expired)
                if self.macro and not self.macro._is_cache_valid():
                    logger.info("--- Macro assessment ---")
                    macro = self.macro.get_macro_assessment()
                    self.portfolio_mgr.set_macro_adjustments(macro.get("adjustments"))

                # Scan (or load from cache if another version just scanned)
                scored, data = self._scan_or_load_cache(scan_max_age=90)
                if scored is None:
                    return

                # Atomic evaluate + execute
                logger.info("--- Evaluating & executing ---")
                self._atomic_evaluate_and_execute(scored, data)

                logger.info(
                    f"=== FULL TRADING CYCLE COMPLETE ({time.time() - t0:.1f}s) ==="
                )
                return

            except Exception as e:
                now = time.time()
                if now >= deadline:
                    logger.error(
                        f"Full cycle failed after {attempt} attempts, deadline reached: {e}"
                    )
                    self.alerts.order_failed("CYCLE", str(e))
                    return
                wait = min(30, deadline - now)
                logger.warning(
                    f"Full cycle attempt {attempt} failed: {e}, retrying in {wait:.0f}s"
                )
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
        logger.info(
            f"Shortlist updated: {len(shortlist)} tickers ({len(held_tickers)} held + top {shortlist_size})"
        )

    def run_rerank_cycle(self):
        """Re-rank the cached shortlist and rebalance portfolio.

        Uses the shortlist from the last full scan — much faster than a full cycle
        since it only fetches ~50 tickers instead of ~500.

        If called before market open (e.g. 9:29:50), analysis runs immediately
        and execution is deferred to market open via a timer.
        """
        if not self.broker.is_market_open():
            if not getattr(self, "_rerank_closed_logged", False):
                logger.info("Market is closed, skipping rerank cycles")
                self._rerank_closed_logged = True
            return
        self._rerank_closed_logged = False

        # If we don't have a shortlist, try loading from cache
        if not self._shortlist:
            cached = scan_cache.load_shortlist()
            if cached:
                self._shortlist = cached
                logger.info(
                    f"Loaded shortlist from cache: {len(self._shortlist)} tickers"
                )

        if not self._shortlist:
            logger.warning("No shortlist cached, running full cycle instead")
            self.run_full_cycle()
            return

        t0 = time.time()
        logger.info(f"=== RE-RANK CYCLE START ({len(self._shortlist)} tickers) ===")

        try:
            # Re-rank uses a shorter cache TTL (30s) since it runs every minute
            lock_fd = scan_cache.acquire_scan_lock(timeout=5.0)

            if lock_fd is None:
                # Another process is re-ranking — use cache if available
                if scan_cache.is_fresh(30):
                    scored, data = scan_cache.load()
                    if scored:
                        self._atomic_evaluate_and_execute(scored, data)
                        logger.info(
                            f"=== RE-RANK CYCLE COMPLETE (from cache, {time.time() - t0:.1f}s) ==="
                        )
                        return
                logger.info("Waiting for scan lock...")
                scored, data = self._wait_for_cache(max_age=30, timeout=60)
                if scored:
                    self._atomic_evaluate_and_execute(scored, data)
                    logger.info(
                        f"=== RE-RANK CYCLE COMPLETE (waited, {time.time() - t0:.1f}s) ==="
                    )
                return

            try:
                # Re-fetch data and re-score only the shortlist
                data = self.screener.get_data_for_tickers(self._shortlist)
                spy_df = self.screener._fetch_single("SPY")
                scored = self.analyzer.analyze_batch(self._shortlist, data, spy_df)

                if not scored:
                    logger.warning("No stocks scored in re-rank")
                    return

                scan_cache.save(scored, data, self._shortlist)
                self._atomic_evaluate_and_execute(scored, data)
                logger.info(f"=== RE-RANK CYCLE COMPLETE ({time.time() - t0:.1f}s) ===")

            finally:
                scan_cache.release_scan_lock(lock_fd)

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

        logger.info(
            f"Market opens in {delay:.0f}s, deferring execution to {open_str} ET"
        )
        timer = threading.Timer(
            delay, self._atomic_evaluate_and_execute, args=[scored, data]
        )
        timer.daemon = True
        timer.start()

    def sync_local_state(self) -> dict:
        """Reconcile local DB with Alpaca: positions and portfolio snapshot.

        - Insert positions held on Alpaca that are missing locally
        - Close local open positions that no longer exist on Alpaca
        - Update qty when Alpaca and DB disagree
        - Record a fresh portfolio snapshot

        Synced-in positions use entry_price=avg_entry (from Alpaca) and
        entry_time=now since the real entry timestamp is unknown. v2/v3
        hold-window logic will therefore treat synced rows as just-bought.

        Returns a summary dict; safe to call without the trade lock when
        no other cycle is running (e.g. the standalone CLI command).
        """
        try:
            alpaca_positions = self.broker.get_positions()
            account = self.broker.get_account()
        except Exception as e:
            logger.error(f"sync_local_state: failed to fetch from Alpaca: {e}")
            return {"error": str(e)}

        db_by_ticker = {p.ticker: p for p in self.db.get_open_positions()}
        alpaca_by_ticker = {p["ticker"]: p for p in alpaca_positions}

        added, closed, qty_updated = [], [], []

        for ticker, ap in alpaca_by_ticker.items():
            if ticker in db_by_ticker:
                continue
            entry = ap["avg_entry"]
            current = ap.get("current_price") or entry
            pos = Position(
                ticker=ticker,
                qty=ap["qty"],
                entry_price=entry,
                entry_time=datetime.now(),
                high_water_mark=max(entry, current),
                status="open",
                sector=self.db.get_stock_sector(ticker) or "",
            )
            self.db.save_position(pos)
            added.append(f"{ticker} qty={ap['qty']} @ ${entry:.2f}")

        for ticker, dp in db_by_ticker.items():
            if ticker in alpaca_by_ticker:
                continue
            self.db.mark_position_synced_closed(dp.id)
            closed.append(ticker)

        for ticker, ap in alpaca_by_ticker.items():
            dp = db_by_ticker.get(ticker)
            if dp and dp.qty != ap["qty"]:
                self.db.update_position(dp.id, qty=ap["qty"])
                qty_updated.append(f"{ticker} {dp.qty}->{ap['qty']}")

        peak = max(self.db.get_peak_value() or 0.0, account["portfolio_value"])
        self.db.save_portfolio_snapshot(
            account["portfolio_value"],
            account["cash"],
            account["portfolio_value"] - account["cash"],
            peak,
        )

        summary = {
            "added": added,
            "closed": closed,
            "qty_updated": qty_updated,
            "cash": account["cash"],
            "equity": account["portfolio_value"],
        }
        if added or closed or qty_updated:
            logger.info(
                f"sync_local_state: +{len(added)} -{len(closed)} ~{len(qty_updated)} "
                f"(added={added} closed={closed} qty={qty_updated})"
            )
        else:
            logger.debug("sync_local_state: already in sync")
        return summary

    def _sync_pending_orders(self) -> dict[str, list[dict]]:
        """Sync pending orders: reconcile fills/cancels and return open orders.

        For buy orders that were submitted but not immediately filled:
        - If now filled on Alpaca: create the position in DB
        - If canceled/expired on Alpaca: mark order as canceled in DB
        Also returns the map of currently open orders on Alpaca.
        """
        # 1. Reconcile DB pending orders against Alpaca
        self._reconcile_pending_buys()
        self._reconcile_pending_sells()

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

    def _reconcile_pending_sells(self):
        """Check DB pending sell orders against Alpaca and reconcile."""
        pending = self.db.get_pending_sell_orders()
        if not pending:
            return

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
                    logger.info(
                        f"Reconciled filled sell for {db_order['ticker']} "
                        f"@ {fill_price}"
                    )

                elif alpaca_status in ("canceled", "cancelled", "expired", "rejected"):
                    self.db.update_order(db_order["id"], status="canceled")
                    logger.info(
                        f"Sell order for {db_order['ticker']} was {alpaca_status}, "
                        f"updated DB"
                    )

                elif alpaca_status in (
                    "new",
                    "accepted",
                    "pending_new",
                    "partially_filled",
                ):
                    pass  # still active, leave as-is

            except Exception as e:
                logger.warning(
                    f"Failed to reconcile sell order {db_order['alpaca_order_id']} "
                    f"for {db_order['ticker']}: {e}"
                )

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
                    self.db.update_order(db_order["id"], status="canceled")
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

    def _cancel_pending_orders_for(
        self, ticker: str, side: str, pending_orders: dict[str, list[dict]]
    ):
        """Cancel all pending orders for a ticker on a given side."""
        for order in pending_orders.get(ticker, []):
            if (
                order["side"].lower().replace("ordersid.", "").replace("orderside.", "")
                == side
                or side == "all"
            ):
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
            # Reconcile any drift between local positions and Alpaca's view
            # (external trades, manual UI edits, post-reset hydration).
            self.sync_local_state()
            pending_buy_tickers = {
                t
                for t, orders in pending_orders.items()
                if any("buy" in o["side"].lower() for o in orders)
            }
            pending_sell_tickers = {
                t
                for t, orders in pending_orders.items()
                if any("sell" in o["side"].lower() for o in orders)
            }

            account = self.broker.get_account()
            positions = self.db.get_open_positions()
            alpaca_positions = self.broker.get_positions()

            # Record today's latest price for each open holding. The final
            # upsert of the day becomes that ticker's closing price, used by
            # prior-close-based profit/loss triggers in PortfolioManager.
            self.db.record_daily_holding_prices(alpaca_positions)

            # Save portfolio snapshot
            peak = max(self.db.get_peak_value(), account["portfolio_value"])
            self.db.save_portfolio_snapshot(
                account["portfolio_value"],
                account["cash"],
                account["portfolio_value"] - account["cash"],
                peak,
            )

            # Generate signals
            signals = self.portfolio_mgr.evaluate(
                scored, positions, account, alpaca_positions, data
            )
            if not signals:
                logger.info("No trading signals generated")
                return

            # Filter out signals for tickers with pending orders on the same side.
            filtered = []
            for s in signals:
                if s.action == "buy" and s.ticker in pending_buy_tickers:
                    logger.info(f"Skipping buy {s.ticker}: pending buy order exists")
                    continue
                if s.action == "sell" and s.ticker in pending_buy_tickers:
                    logger.info(
                        f"Skipping sell {s.ticker}: only has pending buy, no filled position"
                    )
                    continue
                if s.action == "sell" and s.ticker in pending_sell_tickers:
                    logger.info(
                        f"Skipping sell {s.ticker}: pending sell order already exists"
                    )
                    continue
                filtered.append(s)

            if not filtered:
                logger.info("No signals after filtering pending orders")
                return

            # Cash guard: cap aggregate buy cost at available cash to avoid margin.
            # Sells always execute; only buys get scaled or dropped.
            if self.config.trading.get("cash_only", True):
                sells = [s for s in filtered if s.action == "sell"]
                buys = [s for s in filtered if s.action == "buy"]
                buys = self._apply_cash_guard(
                    sells, buys, data, pending_orders, account["cash"]
                )
                # Sells first so fills free cash before any buy submits
                filtered = sells + buys
                if not filtered:
                    logger.info("No signals after cash guard")
                    return

            # Execute signals
            logger.info(f"Executing {len(filtered)} signals (lock held)")
            self._execute_signals(filtered, data, positions, pending_orders)

    def _apply_cash_guard(
        self,
        sell_signals: list[Signal],
        buy_signals: list[Signal],
        data: dict,
        pending_orders: dict[str, list[dict]],
        cash_available: float,
    ) -> list[Signal]:
        """Scale down buy quantities so aggregate buy cost ≤ available cash.

        Budget = cash + expected sell proceeds − value reserved by pending buys.
        If aggregate proposed buy cost exceeds budget, scale each buy's qty
        proportionally. Buys scaled to zero are dropped.
        """
        slippage = self.config.trading.get("cash_slippage_buffer", 0.005)

        def _price(ticker: str) -> float:
            df = data.get(ticker)
            if df is None or df.empty:
                return 0.0
            return float(df["Close"].iloc[-1])

        sell_proceeds = 0.0
        for s in sell_signals:
            p = _price(s.ticker)
            if p > 0:
                sell_proceeds += s.suggested_qty * p * (1 - slippage)

        pending_buy_value = 0.0
        for ticker, orders in pending_orders.items():
            p = _price(ticker)
            if p <= 0:
                continue
            for o in orders:
                if "buy" in o["side"].lower():
                    pending_buy_value += o["qty"] * p * (1 + slippage)

        budget = max(0.0, cash_available + sell_proceeds - pending_buy_value)

        buy_costs = []
        total_cost = 0.0
        for s in buy_signals:
            p = _price(s.ticker)
            if p <= 0:
                continue
            cost = s.suggested_qty * p * (1 + slippage)
            buy_costs.append((s, cost))
            total_cost += cost

        if total_cost <= budget:
            return [s for s, _ in buy_costs]

        if budget <= 0:
            logger.warning(
                f"Cash guard: budget=${budget:,.2f} "
                f"(cash=${cash_available:,.0f} + sells=${sell_proceeds:,.0f} "
                f"− pending_buys=${pending_buy_value:,.0f}); "
                f"dropping {len(buy_costs)} buy(s) totaling ${total_cost:,.0f}"
            )
            return []

        scale = budget / total_cost
        logger.info(
            f"Cash guard: scaling {len(buy_costs)} buy(s) by {scale:.1%} "
            f"(budget=${budget:,.0f} < proposed=${total_cost:,.0f})"
        )

        scaled = []
        for signal, _cost in buy_costs:
            new_qty = int(signal.suggested_qty * scale)
            if new_qty > 0:
                scaled.append(replace(signal, suggested_qty=new_qty))
        return scaled

    def _execute_signals(
        self,
        signals,
        data: dict,
        positions: list[Position],
        pending_orders: dict[str, list[dict]] = None,
    ):
        """Execute a list of buy/sell signals. Caller must hold _trade_lock."""
        pos_map = {p.ticker: p for p in positions}
        pending_orders = pending_orders or {}

        for signal in signals:
            try:
                df = data.get(signal.ticker)
                current_price = (
                    df["Close"].iloc[-1] if df is not None and not df.empty else 0
                )

                if current_price <= 0:
                    logger.warning(f"No price data for {signal.ticker}, skipping")
                    continue

                # Cancel any pending buy orders before selling to avoid
                # the buy filling after we've sold the position
                if signal.action == "sell":
                    self._cancel_pending_orders_for(
                        signal.ticker, "buy", pending_orders
                    )

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
                                existing.id,
                                existing.entry_price,
                                "stale_position_cleanup",
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
                        self.alerts.position_opened(
                            signal.ticker, order.qty, fill_price
                        )

                elif signal.action == "sell":
                    existing = pos_map.get(signal.ticker)
                    if existing:
                        sold_qty = min(signal.suggested_qty, existing.qty)
                        if sold_qty >= existing.qty:
                            # Full sell — close DB position
                            self.db.close_position(
                                existing.id, fill_price, signal.reason
                            )
                        else:
                            # Partial sell — reduce qty in DB
                            self.db.update_position(
                                existing.id, qty=existing.qty - sold_qty
                            )
                        pnl = (fill_price - existing.entry_price) * sold_qty
                        pnl_pct = (
                            (fill_price - existing.entry_price) / existing.entry_price
                        ) * 100
                        txn_logger.info(
                            f"SELL | {signal.ticker} | qty={sold_qty} | "
                            f"entry={existing.entry_price:.2f} | exit={fill_price:.2f} | "
                            f"pnl=${pnl:.2f} ({pnl_pct:+.1f}%) | reason={signal.reason}"
                        )
                        self.alerts.position_closed(
                            signal.ticker,
                            sold_qty,
                            fill_price,
                            signal.reason,
                            pnl,
                        )

            except Exception as e:
                logger.error(f"Failed to execute signal for {signal.ticker}: {e}")
                self.alerts.order_failed(signal.ticker, str(e))
