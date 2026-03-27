"""Portfolio manager — buy/sell decision engine."""

import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from core.config import Config
from core.database import Database
from core.models import Position, ScoreResult, Signal
from portfolio.risk import calculate_position_size, calculate_stop_loss, calculate_take_profit
from portfolio.allocation import get_open_slots, check_sector_limit, check_cash_reserve

logger = logging.getLogger("aitrading.portfolio.manager")


class PortfolioManager:
    """Decides what to buy and sell based on scores and risk rules."""

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
                f"Macro adjustments applied: buy_threshold={adjustments.get('buy_threshold', 0):+d}, "
                f"max_positions={adjustments.get('max_positions', 0):+d}, "
                f"cash_reserve={adjustments.get('cash_reserve_add', 0):+.0%}"
            )

    def _get_effective_param(self, key: str, default):
        """Get a trading parameter with macro adjustments applied."""
        base = self.tc.get(key, default)
        if not self._macro_adjustments:
            return base
        if key == "buy_threshold":
            return base + self._macro_adjustments.get("buy_threshold", 0)
        if key == "max_positions":
            return max(1, base + self._macro_adjustments.get("max_positions", 0))
        if key == "cash_reserve_pct":
            return min(0.50, base + self._macro_adjustments.get("cash_reserve_add", 0))
        return base

    def _get_sector_limit(self, sector: str) -> float:
        """Get sector limit with macro cycle adjustments."""
        default = self.tc.get("max_sector_pct", 0.30)
        if not self._macro_adjustments:
            return default
        sector_limits = self._macro_adjustments.get("sector_limits", {})
        return sector_limits.get(sector, default)

    def evaluate(
        self,
        ranked_candidates: list[ScoreResult],
        positions: list[Position],
        account_info: dict,
        data: dict[str, pd.DataFrame],
    ) -> list[Signal]:
        """
        Evaluate current portfolio against candidates.
        Returns list of buy/sell signals.
        """
        signals = []
        account_value = account_info["portfolio_value"]
        cash = account_info["cash"]

        # --- SELL LOGIC ---
        sell_signals = self._evaluate_sells(ranked_candidates, positions)
        signals.extend(sell_signals)

        # --- DRAWDOWN CHECK ---
        drawdown_signals = self._check_drawdown(account_value, positions)
        if drawdown_signals:
            return drawdown_signals  # Override everything in drawdown

        # --- BUY LOGIC ---
        # Account for pending sells to calculate open slots
        pending_sell_tickers = {s.ticker for s in sell_signals}
        remaining_positions = [p for p in positions if p.ticker not in pending_sell_tickers]

        # Use macro-adjusted max_positions for open slots
        effective_max = self._get_effective_param("max_positions", 10)
        open_slots = max(0, effective_max - len(remaining_positions))

        if open_slots > 0:
            buy_signals = self._evaluate_buys(
                ranked_candidates, remaining_positions, account_value, cash, data, open_slots
            )
            signals.extend(buy_signals)

        logger.info(
            f"Evaluation complete: {len([s for s in signals if s.action == 'buy'])} buys, "
            f"{len([s for s in signals if s.action == 'sell'])} sells"
        )
        return signals

    def _evaluate_sells(
        self, candidates: list[ScoreResult], positions: list[Position]
    ) -> list[Signal]:
        """Sell positions that no longer rank in the top N candidates.

        Pure ranking-based: if a held stock falls below the buy threshold
        or is outranked by a non-held candidate, sell it to make room.
        """
        signals = []
        buy_threshold = self._get_effective_param("buy_threshold", 65)
        max_positions = self._get_effective_param("max_positions", 10)
        score_map = {c.ticker: c for c in candidates}
        held_tickers = {p.ticker for p in positions}

        # Build the ideal portfolio: top N qualifying candidates from universal ranking
        ideal_tickers = set()
        for c in candidates:
            if len(ideal_tickers) >= max_positions:
                break
            if c.composite >= buy_threshold and c.technical >= 50:
                ideal_tickers.add(c.ticker)

        for pos in positions:
            score = score_map.get(pos.ticker)

            # Sell if stock dropped below buy threshold
            if score and score.composite < buy_threshold:
                signals.append(Signal(
                    ticker=pos.ticker, action="sell",
                    reason=f"below_threshold ({score.composite:.1f} < {buy_threshold})",
                    score=score.composite, suggested_qty=pos.qty,
                ))
                continue

            # Sell if stock is no longer in the ideal top-N and a better
            # non-held candidate exists to replace it
            if pos.ticker not in ideal_tickers:
                better = next(
                    (c for c in candidates
                     if c.ticker in ideal_tickers
                     and c.ticker not in held_tickers),
                    None,
                )
                if better:
                    held_score = score.composite if score else 0
                    signals.append(Signal(
                        ticker=pos.ticker, action="sell",
                        reason=f"outranked (#{self._rank_of(pos.ticker, candidates)} → "
                               f"replaced by {better.ticker} scored {better.composite:.0f})",
                        score=held_score, suggested_qty=pos.qty,
                    ))

        return signals

    @staticmethod
    def _rank_of(ticker: str, candidates: list[ScoreResult]) -> str:
        """Return 1-based rank of ticker in candidates list, or '?' if not found."""
        for i, c in enumerate(candidates):
            if c.ticker == ticker:
                return str(i + 1)
        return "?"

    def _evaluate_buys(
        self,
        candidates: list[ScoreResult],
        positions: list[Position],
        account_value: float,
        cash: float,
        data: dict[str, pd.DataFrame],
        open_slots: int,
    ) -> list[Signal]:
        """Select best candidates to buy."""
        signals = []
        held_tickers = {p.ticker for p in positions}
        buy_threshold = self._get_effective_param("buy_threshold", 65)
        cash_reserve_pct = self._get_effective_param("cash_reserve_pct", 0.20)
        tech_min = 50

        for candidate in candidates:
            if len(signals) >= open_slots:
                break

            if candidate.ticker in held_tickers:
                continue

            if candidate.composite < buy_threshold:
                continue

            if candidate.technical < tech_min:
                continue

            ticker = candidate.ticker
            df = data.get(ticker)
            if df is None or df.empty:
                continue

            current_price = df["Close"].iloc[-1]

            # Check sector limit (macro-adjusted per sector)
            sector = self.db.get_stock_sector(ticker)
            sector_limit = self._get_sector_limit(sector)
            if not check_sector_limit(sector, positions, self.config, sector_limit):
                continue

            # Calculate position size
            qty = calculate_position_size(current_price, account_value, df, self.config)
            if qty <= 0:
                continue

            # Check cash reserve
            order_cost = qty * current_price
            if not check_cash_reserve(cash, order_cost, account_value, self.config):
                continue

            stop = calculate_stop_loss(current_price, self.config)
            tp = calculate_take_profit(current_price, self.config)

            signals.append(Signal(
                ticker=ticker, action="buy",
                reason=f"score={candidate.composite:.1f}",
                score=candidate.composite,
                suggested_qty=qty,
                stop_loss=stop,
                take_profit=tp,
            ))

            # Update remaining cash for next candidate
            cash -= order_cost
            held_tickers.add(ticker)

        return signals

    def _check_drawdown(
        self, current_value: float, positions: list[Position]
    ) -> list[Signal]:
        """Check portfolio drawdown and trigger protective sells if needed."""
        peak = self.db.get_peak_value()
        if peak <= 0:
            return []

        drawdown = (peak - current_value) / peak

        liquidate_threshold = self.tc.get("drawdown_liquidate_pct", 0.15)
        reduce_threshold = self.tc.get("drawdown_reduce_pct", 0.10)

        if drawdown >= liquidate_threshold:
            logger.critical(
                f"DRAWDOWN LIQUIDATION: {drawdown:.1%} from peak ${peak:.0f}"
            )
            return [
                Signal(
                    ticker=p.ticker, action="sell",
                    reason=f"drawdown_liquidation ({drawdown:.1%})",
                    suggested_qty=p.qty,
                )
                for p in positions
            ]

        if drawdown >= reduce_threshold:
            logger.warning(
                f"DRAWDOWN REDUCTION: {drawdown:.1%} from peak ${peak:.0f}"
            )
            return [
                Signal(
                    ticker=p.ticker, action="sell",
                    reason=f"drawdown_reduction ({drawdown:.1%})",
                    suggested_qty=max(1, p.qty // 2),
                )
                for p in positions
            ]

        return []
