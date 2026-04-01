"""Portfolio manager — profit-based sells + score-proportional redistribution."""

import logging
from typing import Optional

import pandas as pd

from core.config import Config
from core.database import Database
from core.models import Position, ScoreResult, Signal

logger = logging.getLogger("aitrading.portfolio.manager")


class PortfolioManager:
    """Decides what to buy and sell based on profit thresholds and score allocation."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.tc = config.trading
        self._macro_adjustments = None

    def set_macro_adjustments(self, adjustments: Optional[dict]):
        """Apply macro-economic parameter adjustments for this cycle."""
        self._macro_adjustments = adjustments
        if adjustments:
            logger.info(
                f"Macro adjustments applied: buy_threshold={adjustments.get('buy_threshold', 0):+d}"
            )

    def _get_effective_param(self, key: str, default):
        """Get a trading parameter with macro adjustments applied."""
        base = self.tc.get(key, default)
        if not self._macro_adjustments:
            return base
        if key == "buy_threshold":
            return base + self._macro_adjustments.get("buy_threshold", 0)
        return base

    def evaluate(
        self,
        ranked_candidates: list[ScoreResult],
        positions: list[Position],
        account_info: dict,
        alpaca_positions: list[dict],
        data: dict[str, pd.DataFrame],
    ) -> list[Signal]:
        """
        Two-step portfolio evaluation:
        1. Profit-based sells: sell if P&L >= +1% or <= -0.5% (from Alpaca avg cost)
        2. Score-based redistribution: allocate purchase power proportionally by score
        """
        signals = []
        portfolio_value = account_info["portfolio_value"]

        alpaca_map = {p["ticker"]: p for p in alpaca_positions}

        # --- STEP 1: Profit-based sells ---
        profit_sell_signals, profit_sold_tickers = self._profit_based_sells(
            positions, alpaca_map
        )
        signals.extend(profit_sell_signals)

        # --- STEP 2: Score-based redistribution ---
        redistribution_signals = self._redistribute(
            ranked_candidates, positions, alpaca_map,
            portfolio_value, data, profit_sold_tickers,
        )
        signals.extend(redistribution_signals)

        buy_count = len([s for s in signals if s.action == "buy"])
        sell_count = len([s for s in signals if s.action == "sell"])
        logger.info(f"Evaluation complete: {buy_count} buys, {sell_count} sells")
        return signals

    def _profit_based_sells(
        self,
        positions: list[Position],
        alpaca_map: dict[str, dict],
    ) -> tuple[list[Signal], set[str]]:
        """Step 1: Sell positions that hit profit or loss thresholds.

        Returns (sell_signals, tickers_sold) — sold tickers get 2-hour cooldown.
        """
        profit_take = self.tc.get("profit_take_pct", 0.01)
        loss_cut = self.tc.get("loss_cut_pct", 0.005)
        signals = []
        sold_tickers = set()

        for pos in positions:
            live = alpaca_map.get(pos.ticker)
            if not live:
                continue

            avg_entry = live["avg_entry"]
            current_price = live["current_price"]
            if avg_entry <= 0:
                continue

            pnl_pct = (current_price - avg_entry) / avg_entry

            if pnl_pct >= profit_take:
                signals.append(Signal(
                    ticker=pos.ticker, action="sell",
                    reason=f"profit_take ({pnl_pct:+.2%})",
                    score=0, suggested_qty=pos.qty,
                ))
                sold_tickers.add(pos.ticker)
            elif pnl_pct <= -loss_cut:
                signals.append(Signal(
                    ticker=pos.ticker, action="sell",
                    reason=f"loss_cut ({pnl_pct:+.2%})",
                    score=0, suggested_qty=pos.qty,
                ))
                sold_tickers.add(pos.ticker)

        return signals, sold_tickers

    def _redistribute(
        self,
        candidates: list[ScoreResult],
        positions: list[Position],
        alpaca_map: dict[str, dict],
        portfolio_value: float,
        data: dict[str, pd.DataFrame],
        profit_sold_tickers: set[str],
    ) -> list[Signal]:
        """Step 2: Redistribute capital proportionally by score.

        Allocates purchase_power_pct of portfolio value across qualifying stocks.
        Generates buy/sell signals to reach target quantities.
        """
        buy_threshold = self._get_effective_param("buy_threshold", 60)
        purchase_power_pct = self.tc.get("purchase_power_pct", 0.50)
        cooldown_hours = self.tc.get("cooldown_hours", 2)

        # Get tickers on cooldown from prior profit/loss sells
        cooldown_tickers = self.db.get_recently_profit_sold(cooldown_hours)
        # Also exclude tickers being sold this cycle
        excluded = profit_sold_tickers | cooldown_tickers
        if cooldown_tickers:
            logger.info(f"Cooldown active for: {cooldown_tickers}")

        # Filter qualifying candidates
        qualifying = [
            c for c in candidates
            if c.composite >= buy_threshold and c.ticker not in excluded
        ]

        if not qualifying:
            # Sell all held positions that aren't being sold in step 1
            return self._sell_non_qualifying(positions, profit_sold_tickers)

        # Calculate proportional allocation
        total_score = sum(c.composite for c in qualifying)
        available_capital = portfolio_value * purchase_power_pct

        # Build current holdings map (qty from Alpaca for accuracy)
        held_map = {}
        for pos in positions:
            if pos.ticker not in profit_sold_tickers:
                live = alpaca_map.get(pos.ticker)
                held_map[pos.ticker] = live["qty"] if live else pos.qty

        qualifying_tickers = {c.ticker for c in qualifying}
        signals = []

        # Generate signals to reach target allocation
        for c in qualifying:
            target_pct = c.composite / total_score
            target_dollars = available_capital * target_pct

            # Get current price
            live = alpaca_map.get(c.ticker)
            if live:
                current_price = live["current_price"]
            else:
                df = data.get(c.ticker)
                if df is None or df.empty:
                    continue
                current_price = df["Close"].iloc[-1]

            if current_price <= 0:
                continue

            target_qty = int(target_dollars / current_price)
            current_qty = held_map.get(c.ticker, 0)

            if target_qty > current_qty:
                # Buy more
                buy_qty = target_qty - current_qty
                if buy_qty > 0:
                    signals.append(Signal(
                        ticker=c.ticker, action="buy",
                        reason=f"redistribution (score={c.composite:.1f}, "
                               f"target={target_pct:.1%})",
                        score=c.composite, suggested_qty=buy_qty,
                    ))
            elif target_qty < current_qty:
                # Sell excess (no cooldown)
                sell_qty = current_qty - target_qty
                if sell_qty > 0:
                    signals.append(Signal(
                        ticker=c.ticker, action="sell",
                        reason=f"redistribution_reduce (score={c.composite:.1f}, "
                               f"target={target_pct:.1%})",
                        score=c.composite, suggested_qty=sell_qty,
                    ))

        # Sell positions that no longer qualify (no cooldown)
        for pos in positions:
            if (
                pos.ticker not in qualifying_tickers
                and pos.ticker not in profit_sold_tickers
            ):
                signals.append(Signal(
                    ticker=pos.ticker, action="sell",
                    reason="no_longer_qualifies",
                    score=0, suggested_qty=pos.qty,
                ))

        return signals

    def _sell_non_qualifying(
        self, positions: list[Position], already_sold: set[str]
    ) -> list[Signal]:
        """Sell all held positions that aren't already being sold."""
        signals = []
        for pos in positions:
            if pos.ticker not in already_sold:
                signals.append(Signal(
                    ticker=pos.ticker, action="sell",
                    reason="no_longer_qualifies",
                    score=0, suggested_qty=pos.qty,
                ))
        return signals
