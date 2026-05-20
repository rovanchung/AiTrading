"""Portfolio manager — profit-based sells + score-proportional redistribution."""

import logging
from collections import Counter
from datetime import datetime
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

    @property
    def _is_v2(self) -> bool:
        """True for any account using v2 strategy (v2, v3, etc.)."""
        return self.tc.get("strategy_version", "v1") == "v2"

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
        1. Profit-based sells: sell if P&L >= +profit_take% or <= -loss_cut%
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
            ranked_candidates,
            positions,
            alpaca_map,
            portfolio_value,
            data,
            profit_sold_tickers,
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

        Returns (sell_signals, tickers_sold) — sold tickers get cooldown.
        """
        if self._is_v2:
            profit_take = self.tc.get("v2_profit_take_pct", 0.03)
            loss_cut = self.tc.get("v2_loss_cut_pct", 0.02)
            profit_take_prior = self.tc.get("v2_profit_take_vs_prior_close_pct", 0.0)
            loss_cut_prior = self.tc.get("v2_loss_cut_vs_prior_close_pct", 0.0)
            min_hold_minutes = self.tc.get("v2_min_hold_minutes", 30)
        else:
            profit_take = self.tc.get("profit_take_pct", 0.01)
            loss_cut = self.tc.get("loss_cut_pct", 0.005)
            profit_take_prior = self.tc.get("profit_take_vs_prior_close_pct", 0.0)
            loss_cut_prior = self.tc.get("loss_cut_vs_prior_close_pct", 0.0)
            min_hold_minutes = 0

        prior_close_enabled = profit_take_prior > 0 or loss_cut_prior > 0

        signals = []
        sold_tickers = set()

        for pos in positions:
            live = alpaca_map.get(pos.ticker)
            if not live:
                continue

            # v2: skip positions not held long enough
            if min_hold_minutes > 0 and pos.entry_time:
                held_minutes = (datetime.now() - pos.entry_time).total_seconds() / 60
                if held_minutes < min_hold_minutes:
                    continue

            avg_entry = live["avg_entry"]
            current_price = live["current_price"]
            if avg_entry <= 0:
                continue

            pnl_pct = (current_price - avg_entry) / avg_entry
            sell_reason: Optional[str] = None

            if pnl_pct >= profit_take:
                sell_reason = f"profit_take ({pnl_pct:+.2%})"
            elif pnl_pct <= -loss_cut:
                sell_reason = f"loss_cut ({pnl_pct:+.2%})"

            # Prior-close triggers — only checked when avg-cost rules didn't
            # already fire, so existing behavior is unchanged when the new
            # configs stay at 0.
            if sell_reason is None and prior_close_enabled:
                prior_close = self.db.get_prior_close(pos.ticker)
                if prior_close and prior_close > 0:
                    prior_pct = (current_price - prior_close) / prior_close
                    if profit_take_prior > 0 and prior_pct >= profit_take_prior:
                        sell_reason = f"profit_take_prior_close ({prior_pct:+.2%})"
                    elif loss_cut_prior > 0 and prior_pct <= -loss_cut_prior:
                        sell_reason = f"loss_cut_prior_close ({prior_pct:+.2%})"

            if sell_reason:
                signals.append(
                    Signal(
                        ticker=pos.ticker,
                        action="sell",
                        reason=sell_reason,
                        score=0,
                        suggested_qty=pos.qty,
                    )
                )
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

        if self._is_v2:
            sell_threshold = self.tc.get("v2_sell_threshold", 55)
            min_share_diff = self.tc.get("v2_rebalance_min_share_diff", 1.0)
            profit_take_prior = self.tc.get("v2_profit_take_vs_prior_close_pct", 0.0)
            loss_cut_prior = self.tc.get("v2_loss_cut_vs_prior_close_pct", 0.0)
        else:
            sell_threshold = buy_threshold  # no hysteresis in v1
            min_share_diff = 0.0
            profit_take_prior = self.tc.get("profit_take_vs_prior_close_pct", 0.0)
            loss_cut_prior = self.tc.get("loss_cut_vs_prior_close_pct", 0.0)

        prior_close_enabled = profit_take_prior > 0 or loss_cut_prior > 0

        # Concentration caps (0 = disabled). Applied to NEW buys only:
        # - max_pct_per_position: ceiling on a single name's allocation
        # - max_sector_pct: ceiling on combined slots in one GICS sector
        max_per_pos_pct = self.tc.get("max_pct_per_position", 0.0)
        max_sector_pct = self.tc.get("max_sector_pct", 0.0)
        max_positions = self.tc.get("max_positions", 10)
        sector_counts = Counter(p.sector for p in positions if p.sector)
        max_in_sector = (
            int(max_positions * max_sector_pct) if max_sector_pct > 0 else None
        )

        # Get tickers on cooldown from prior profit/loss sells
        cooldown_tickers = self.db.get_recently_profit_sold(cooldown_hours)
        # Also exclude tickers being sold this cycle
        excluded = profit_sold_tickers | cooldown_tickers
        if cooldown_tickers:
            logger.info(f"[redistribute] cooldown active: {sorted(cooldown_tickers)}")

        # Build score lookup
        score_map = {c.ticker: c.composite for c in candidates}

        # Tickers we currently hold (excluding those being sold this cycle).
        # Held positions with score >= sell_threshold are included in the
        # qualifying set so they participate in proportional allocation
        # rather than free-riding outside the redistribution budget.
        held_tickers = {
            p.ticker for p in positions if p.ticker not in profit_sold_tickers
        }

        # Filter qualifying candidates: above buy_threshold (new entries OK),
        # or currently held and still above sell_threshold (hysteresis).
        qualifying = [
            c
            for c in candidates
            if c.ticker not in excluded
            and (
                c.composite >= buy_threshold
                or (c.ticker in held_tickers and c.composite >= sell_threshold)
            )
        ]

        available_capital = portfolio_value * purchase_power_pct
        logger.info(
            f"[redistribute] portfolio=${portfolio_value:,.2f} "
            f"purchase_power={purchase_power_pct:.0%} "
            f"available=${available_capital:,.2f} "
            f"buy_threshold={buy_threshold} sell_threshold={sell_threshold} "
            f"min_share_diff={min_share_diff:.2f} | "
            f"candidates={len(candidates)} qualifying={len(qualifying)} "
            f"held={len(positions)}"
        )

        if not qualifying:
            logger.info(
                "[redistribute] no qualifying candidates; "
                "checking held positions for non-qualifying sells"
            )
            # Sell all held positions that aren't being sold in step 1
            # v2: respect sell_threshold hysteresis
            return self._sell_non_qualifying(
                positions,
                profit_sold_tickers,
                score_map,
                sell_threshold,
            )

        # Calculate proportional allocation
        total_score = sum(c.composite for c in qualifying)

        # Build current holdings map (qty from Alpaca for accuracy)
        held_map = {}
        for pos in positions:
            if pos.ticker not in profit_sold_tickers:
                live = alpaca_map.get(pos.ticker)
                held_map[pos.ticker] = live["qty"] if live else pos.qty

        qualifying_tickers = {c.ticker for c in qualifying}
        signals = []
        stats = {
            "buy": 0,
            "sell_reduce": 0,
            "hold_in_band": 0,
            "skip_min_share_diff": 0,
            "skip_min_hold": 0,
            "skip_no_price": 0,
            "skip_no_score": 0,
            "no_longer_qualifies": 0,
            "held_above_sell_threshold": 0,
        }
        planned_target = 0.0  # sum of target_qty * price across qualifying
        planned_buy_cost = 0.0
        planned_sell_value = 0.0

        # Generate signals to reach target allocation
        for c in qualifying:
            target_pct = c.composite / total_score
            target_dollars = available_capital * target_pct

            # Cap a single name at max_pct_per_position of total equity
            if max_per_pos_pct > 0:
                cap_dollars = portfolio_value * max_per_pos_pct
                if target_dollars > cap_dollars:
                    target_dollars = cap_dollars
                    target_pct = (
                        cap_dollars / available_capital if available_capital > 0 else 0
                    )

            # Get current price
            live = alpaca_map.get(c.ticker)
            if live:
                current_price = live["current_price"]
            else:
                df = data.get(c.ticker)
                if df is None or df.empty:
                    logger.info(
                        f"[redistribute] {c.ticker} score={c.composite:.1f} "
                        f"target={target_pct:.1%}(${target_dollars:,.2f}) → "
                        f"skip: no price data"
                    )
                    stats["skip_no_price"] += 1
                    continue
                current_price = df["Close"].iloc[-1]

            if current_price <= 0:
                logger.info(
                    f"[redistribute] {c.ticker} score={c.composite:.1f} "
                    f"target={target_pct:.1%}(${target_dollars:,.2f}) → "
                    f"skip: price={current_price}"
                )
                stats["skip_no_price"] += 1
                continue

            target_qty = int(target_dollars / current_price)
            current_qty = held_map.get(c.ticker, 0)
            current_dollars = current_qty * current_price
            target_qty_dollars = target_qty * current_price
            planned_target += target_qty_dollars

            base = (
                f"[redistribute] {c.ticker} score={c.composite:.1f} "
                f"target={target_pct:.1%}(${target_dollars:,.2f}) "
                f"price=${current_price:,.2f} "
                f"target_qty={target_qty} held={current_qty}"
            )

            if target_qty > current_qty:
                buy_qty = target_qty - current_qty
                # Prior-close pre-check: if today's price already trips the
                # prior-close loss-cut / profit-take threshold, skip the buy.
                # Otherwise we'd allocate capital this cycle and the very next
                # _profit_based_sells pass would immediately close it.
                if prior_close_enabled:
                    prior_close = self.db.get_prior_close(c.ticker)
                    if prior_close and prior_close > 0:
                        prior_pct = (current_price - prior_close) / prior_close
                        trips_loss = loss_cut_prior > 0 and prior_pct <= -loss_cut_prior
                        trips_profit = (
                            profit_take_prior > 0 and prior_pct >= profit_take_prior
                        )
                        if trips_loss or trips_profit:
                            logger.info(
                                f"{base} prior_pct={prior_pct:+.2%} → "
                                f"skip: would trip prior-close rule on entry"
                            )
                            stats.setdefault("skip_prior_close_trip", 0)
                            stats["skip_prior_close_trip"] += 1
                            continue
                # v2: skip if the dollar gap is worth less than min_share_diff
                # shares at the current price (only applies when already
                # holding — initial entries always pass)
                if min_share_diff > 0 and current_qty > 0:
                    share_diff = abs(target_dollars - current_dollars) / current_price
                    if share_diff <= min_share_diff:
                        logger.info(
                            f"{base} gap={share_diff:.2f}sh → skip: below min_share_diff"
                        )
                        stats["skip_min_share_diff"] += 1
                        continue
                # Sector cap on NEW positions only — top-ups to held names are
                # already counted in the current sector tally.
                if max_in_sector is not None and current_qty == 0:
                    sector = self.db.get_stock_sector(c.ticker) or ""
                    if sector and sector_counts.get(sector, 0) >= max_in_sector:
                        logger.info(
                            f"{base} sector={sector} "
                            f"({sector_counts[sector]}/{max_in_sector}) → "
                            f"skip: sector cap"
                        )
                        stats.setdefault("skip_sector_cap", 0)
                        stats["skip_sector_cap"] += 1
                        continue
                    if sector:
                        sector_counts[sector] += 1
                cost = buy_qty * current_price
                planned_buy_cost += cost
                stats["buy"] += 1
                logger.info(f"{base} → BUY {buy_qty} (~${cost:,.2f})")
                signals.append(
                    Signal(
                        ticker=c.ticker,
                        action="buy",
                        reason=f"redistribution (score={c.composite:.1f}, "
                        f"target={target_pct:.1%})",
                        score=c.composite,
                        suggested_qty=buy_qty,
                    )
                )
            elif target_qty < current_qty:
                sell_qty = current_qty - target_qty
                # v2: skip if the dollar gap is worth less than min_share_diff
                # shares at the current price
                if min_share_diff > 0:
                    share_diff = abs(target_dollars - current_dollars) / current_price
                    if share_diff <= min_share_diff:
                        logger.info(
                            f"{base} gap={share_diff:.2f}sh → skip: below min_share_diff"
                        )
                        stats["skip_min_share_diff"] += 1
                        continue
                proceeds = sell_qty * current_price
                planned_sell_value += proceeds
                stats["sell_reduce"] += 1
                logger.info(f"{base} → SELL {sell_qty} (~${proceeds:,.2f})")
                signals.append(
                    Signal(
                        ticker=c.ticker,
                        action="sell",
                        reason=f"redistribution_reduce (score={c.composite:.1f}, "
                        f"target={target_pct:.1%})",
                        score=c.composite,
                        suggested_qty=sell_qty,
                    )
                )
            else:
                stats["hold_in_band"] += 1
                logger.info(f"{base} → hold (already at target)")

        # Sell positions that no longer qualify
        for pos in positions:
            if (
                pos.ticker not in qualifying_tickers
                and pos.ticker not in profit_sold_tickers
            ):
                # No fresh score this cycle (data fetch dropped this ticker) →
                # defer the decision rather than treating absence as score=0.
                if pos.ticker not in score_map:
                    stats["skip_no_score"] += 1
                    logger.info(
                        f"[redistribute] {pos.ticker} no fresh score this cycle "
                        f"→ skip: no_score (defer no_longer_qualifies decision)"
                    )
                    continue
                ticker_score = score_map[pos.ticker]
                live = alpaca_map.get(pos.ticker)
                price = live["current_price"] if live else 0.0
                pos_value = pos.qty * price
                # v2: only sell if score dropped below sell_threshold (hysteresis)
                if ticker_score >= sell_threshold:
                    stats["held_above_sell_threshold"] += 1
                    logger.info(
                        f"[redistribute] {pos.ticker} score={ticker_score:.1f} "
                        f"below buy_threshold but above sell_threshold "
                        f"({sell_threshold}) → hold"
                    )
                    continue
                planned_sell_value += pos_value
                stats["no_longer_qualifies"] += 1
                logger.info(
                    f"[redistribute] {pos.ticker} score={ticker_score:.1f} "
                    f"qty={pos.qty} (~${pos_value:,.2f}) → "
                    f"SELL: no_longer_qualifies (score<{sell_threshold})"
                )
                signals.append(
                    Signal(
                        ticker=pos.ticker,
                        action="sell",
                        reason="no_longer_qualifies",
                        score=0,
                        suggested_qty=pos.qty,
                    )
                )

        rounding_gap = max(0.0, available_capital - planned_target)
        gap_pct = (rounding_gap / available_capital) if available_capital > 0 else 0.0
        total_sells = stats["sell_reduce"] + stats["no_longer_qualifies"]
        logger.info(
            f"[redistribute] summary: "
            f"buys={stats['buy']} (~${planned_buy_cost:,.2f}) "
            f"sells={total_sells} (~${planned_sell_value:,.2f}) | "
            f"skip_min_share_diff={stats['skip_min_share_diff']} "
            f"skip_min_hold={stats['skip_min_hold']} "
            f"skip_no_score={stats['skip_no_score']} "
            f"hold_in_band={stats['hold_in_band']} "
            f"held_above_sell_threshold={stats['held_above_sell_threshold']} | "
            f"available=${available_capital:,.2f} "
            f"planned_deployed=${planned_target:,.2f} "
            f"rounding_gap=${rounding_gap:,.2f} ({gap_pct:.1%} of available)"
        )

        return signals

    def _sell_non_qualifying(
        self,
        positions: list[Position],
        already_sold: set[str],
        score_map: dict[str, float] = None,
        sell_threshold: float = 0,
    ) -> list[Signal]:
        """Sell all held positions that aren't already being sold."""
        signals = []
        for pos in positions:
            if pos.ticker not in already_sold:
                # v2: respect hysteresis — keep if score still above sell_threshold
                if score_map and sell_threshold > 0:
                    # No fresh score this cycle → defer rather than sell on absence
                    if pos.ticker not in score_map:
                        logger.info(
                            f"[sell_non_qualifying] {pos.ticker} no fresh score "
                            f"this cycle → skip"
                        )
                        continue
                    ticker_score = score_map[pos.ticker]
                    if ticker_score >= sell_threshold:
                        continue
                signals.append(
                    Signal(
                        ticker=pos.ticker,
                        action="sell",
                        reason="no_longer_qualifies",
                        score=0,
                        suggested_qty=pos.qty,
                    )
                )
        return signals
