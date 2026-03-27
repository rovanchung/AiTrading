"""Full trading pipeline: scan → analyze → decide → execute."""

import logging
from datetime import datetime

import yfinance as yf

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

    def pre_market_prep(self):
        """Run before market open: refresh universe + macro assessment."""
        logger.info("=== Pre-market prep ===")
        max_age = self.config.get("data.universe_refresh_days", 7)
        refresh_universe(self.db, max_age_days=max_age)

        # Run macro assessment
        logger.info("--- Macro assessment ---")
        macro = self.macro.get_macro_assessment()
        self.portfolio_mgr.set_macro_adjustments(macro.get("adjustments"))
        self.alerts.macro_update(macro)

        logger.info("Pre-market prep complete")

    def run_full_cycle(self):
        """Run the complete scan → analyze → trade pipeline."""
        logger.info("=" * 60)
        logger.info("=== FULL TRADING CYCLE START ===")
        logger.info("=" * 60)

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

        # Step 4: Macro assessment (if not already done in pre-market)
        if not self.macro._is_cache_valid():
            logger.info("--- Step 3b: Macro assessment ---")
            macro = self.macro.get_macro_assessment()
            self.portfolio_mgr.set_macro_adjustments(macro.get("adjustments"))

        # Step 5: Get current state
        logger.info("--- Step 4: Evaluating portfolio ---")
        account = self.broker.get_account()
        positions = self.db.get_open_positions()

        # Save portfolio snapshot
        peak = max(self.db.get_peak_value(), account["portfolio_value"])
        self.db.save_portfolio_snapshot(
            account["portfolio_value"], account["cash"],
            account["portfolio_value"] - account["cash"], peak
        )

        # Step 5: Generate signals
        signals = self.portfolio_mgr.evaluate(scored, positions, account, data)
        if not signals:
            logger.info("No trading signals generated")
            return

        # Step 6: Execute signals
        logger.info(f"--- Step 5: Executing {len(signals)} signals ---")
        self._execute_signals(signals, data, positions)

        logger.info("=== FULL TRADING CYCLE COMPLETE ===")

    def rescore_holdings(self):
        """Re-score existing positions to detect score decay."""
        if not self.broker.is_market_open():
            return

        positions = self.db.get_open_positions()
        if not positions:
            return

        tickers = [p.ticker for p in positions]
        logger.info(f"Re-scoring {len(tickers)} held positions...")

        data = self.screener.get_data_for_tickers(tickers)
        spy_df = self.screener._fetch_single("SPY")
        scored = self.analyzer.analyze_batch(tickers, data, spy_df)

        # Check for sell signals from score decay
        account = self.broker.get_account()
        signals = self.portfolio_mgr.evaluate(scored, positions, account, data)

        sell_signals = [s for s in signals if s.action == "sell"]
        if sell_signals:
            logger.info(f"Rescore found {len(sell_signals)} sell signals")
            self._execute_signals(sell_signals, data, positions)

    def _execute_signals(
        self, signals, data: dict, positions: list[Position]
    ):
        """Execute a list of buy/sell signals."""
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
