# Performance Analysis & Improvement Plan (V2 & V4)

**Analysis Date:** 2026-04-15
**Period:** V2 running since April 2 (13 days), V4 since April 7 (8 days)

---

## Overall Numbers

| Metric | V2 (13 days) | V4 (8 days) |
|--------|-------------|-------------|
| Return | +$245 (+0.25%) | +$660 (+0.66%) |
| Closed trades | 84 | 152 |
| Win rate | 33% | 44% |
| Avg win | $24.14 | $36.47 |
| Avg loss | -$11.24 | -$18.67 |
| Open positions | 54 | 53 |

### P&L by Exit Reason

**V2:**

| Exit Reason | Trades | Total P&L | Avg P&L | Win Rate |
|-------------|--------|-----------|---------|----------|
| profit_take (+3%) | 14 | +$572 | +$40.86 | 100% |
| loss_cut (-2%) | 22 | -$432 | -$19.64 | 9% |
| no_longer_qualifies | 24 | -$93 | -$3.87 | 38% |
| redistribution_reduce | 4 | +$6 | +$1.56 | 75% |
| stale_position_cleanup | 21 | $0 | $0 | 0% |

**V4:**

| Exit Reason | Trades | Total P&L | Avg P&L | Win Rate |
|-------------|--------|-----------|---------|----------|
| profit_take (+5%) | 38 | +$2,032 | +$53.47 | 100% |
| loss_cut (-3%) | 44 | -$1,012 | -$23.00 | 14% |
| no_longer_qualifies | 67 | -$164 | -$2.45 | 31% |
| redistribution_reduce | 3 | +$51 | +$17.00 | 100% |

---

## Root Causes

### Root Cause #1: Sell-Then-Rebuy Churn (THE BIGGEST PROBLEM)

> Fixes: [#1 Increase cooldown to 24h](#1-increase-cooldown), [#10 Re-entry premium](#10-re-entry-premium)

The system sells at a profit, then immediately re-buys the same stock at the higher price, only to get stopped out at a loss. This is destroying profits.

**Example -- INTC (April 15):**
```
BUY  $62.72 -> SELL $65.15 profit_take (+3.29%) = +$46.16
BUY  $65.33 -> SELL $64.10 loss_cut (-2.03%)   = -$20.99
Net: only +$25 instead of +$46
```

**Example -- COHR (April 15, v4):**
```
BUY $321.64 -> SELL $313.55 loss_cut (-3.00%) = -$24.27  (bought HIGH, stopped out)
BUY $310.99 (still holding)
```

**Example -- STX across days:**
```
BUY $494.58 -> SELL $528.91 profit (+5.01%) = +$68.65
BUY $529.16 -> SELL $515.22 loss_cut (-3.21%) = -$27.88
BUY $510.32 -> SELL $514.90 redistribution    = +$9.16
BUY $515.16 (still holding)
```

The 2-hour cooldown is far too short. By the time cooldown expires, the stock is often at its intraday high, and the re-buy gets caught in a mean reversion.

**Most-churned tickers (V4):** EIX (6 trades), CIEN (6), STX (5), DELL (5), COHR (5), INTC (4)

### Root Cause #2: Scoring Is Not Selective Enough

> Fixes: [#2 Raise buy threshold to 68](#2-raise-buy-threshold), [#5 Reduce purchase_power_pct](#5-reduce-purchase-power), [#6 Fix momentum inflation](#6-fix-momentum), [#7 Trend-quality filter](#7-trend-quality)

54% of all scored stocks pass the buy threshold of 60. That's not a filter -- it lets in the majority of the S&P 500.

| Component | Avg Score | Weight | Problem |
|-----------|-----------|--------|---------|
| Technical | 56.7 | 35% | Reasonable |
| Fundamental | 44.5 | 25% | Very weak -- drags composite down but momentum compensates |
| Momentum | **78.0** | 25% | Inflated -- most stocks trend up in a bull market |
| Sentiment | 63.0 | 15% | Keyword-based, crude |

Momentum scoring is particularly generous: nearly every S&P 500 stock with positive 1m/3m returns scores 60+ on momentum alone. This inflates composites past the buy threshold regardless of other signals.

### Root Cause #3: `no_longer_qualifies` Is a Loss Engine

> Fixes: [#3 Widen sell threshold hysteresis to 50](#3-widen-hysteresis)

| Account | Trades | Total P&L | Avg P&L |
|---------|--------|-----------|---------|
| V2 | 24 | -$93 | -$3.87 |
| V4 | 67 | -$164 | -$2.45 |

Stocks hover near the 55-60 boundary (buy threshold 60, sell threshold 55). A stock scores 61, gets bought, drops to 54, gets sold at a loss, then bounces back to 60 and gets re-bought. The 5-point hysteresis band isn't wide enough.

### Root Cause #4: V4 `min_hold_minutes: 0` Causes Instant Flips

> Fixes: [#4 Set V4 min_hold_minutes to 30](#4-fix-v4-hold-time)

V4 has no minimum hold time. Result: NUE was bought and sold in 0 minutes (entry 06:47:25, exit 06:47:26). This produces absurd churn.

### Root Cause #5: Too Many Positions (54/53 open)

> Fixes: [#2 Raise buy threshold](#2-raise-buy-threshold), [#5 Reduce purchase_power_pct](#5-reduce-purchase-power)

With 50%+ of portfolio deployed across 54 positions, each position gets ~$1,100. The profit/loss per trade is tiny ($24 avg win, $11-18 avg loss). A single bad loss_cut (-$66 on WAB) wipes out 3 winning trades. With fewer, larger positions (e.g., 15-20 stocks at ~$3,000 each), each winner would contribute more meaningfully and the system would be forced to be more selective.

### Root Cause #6: 21 Sell Failures in V2

> Resolution: Accounts reset on Alpaca. Local databases cleaned up (positions, orders, snapshots cleared). Will verify sync in the new version.

V2 has 21 failed sell orders ("no open position on Alpaca") suggesting DB-Alpaca position sync issues that may have caused missed exits.

---

## Decision Making

The following improvements will be implemented to address root causes #1, #2, #3, and #4.

### Approved for Implementation

| # | Change | Type | Root Cause | Detail |
|---|--------|------|------------|--------|
| 1 | Increase cooldown to 24h | config | #1 | `cooldown_hours: 2` -> `cooldown_hours: 24` |
| 2 | Raise buy threshold to 68 | config | #2, #5 | `buy_threshold: 60` -> `buy_threshold: 68` |
| 3 | Update sell threshold to 58 | config | #3 | `v2_sell_threshold: 55` -> `v2_sell_threshold: 58` (10-point gap with buy=68) |
| 12 | Add `trade_history` table | code | -- | Separate trade journal recording entry/exit/P&L/hold-time per round-trip |
| -- | Clean up V2/V4 databases | maintenance | #6 | Clear positions, orders, snapshots for fresh start |

### Deferred (future consideration)

| # | Change | Type | Root Cause | Reason Deferred |
|---|--------|------|------------|-----------------|
| 4 | Fix V4 min_hold_minutes to 30 | config | #4 | Intentionally kept at 0 -- V4 tests no-hold behavior vs V2's 30min hold |
| 5 | Reduce purchase_power_pct to 0.30 | config | #5 | Evaluate after threshold changes reduce position count naturally |
| 6 | Fix momentum scoring inflation | code | #2 | Tier 2 -- evaluate after config changes |
| 7 | Add trend-quality filter | code | #2 | Tier 2 -- evaluate after config changes |
| 8 | Improve sentiment scoring | code | #2 | Tier 2 -- evaluate after config changes |
| 9 | Make fundamental scoring time-aware | code | #2 | Tier 2 -- evaluate after config changes |
| 10 | Add re-entry premium | code | #1 | Tier 3 -- cooldown increase may be sufficient |
| 11 | Position sizing by conviction | code | #5 | Tier 3 -- evaluate after threshold changes |
| 12 | ~~Track realized P&L in trade_history~~ | ~~code~~ | -- | **Moved to Approved** |
| 13 | Add daily P&L stop | code | -- | Tier 3 -- safety net |

### Config Changes Summary

```yaml
# BEFORE                          # AFTER
buy_threshold: 60                  buy_threshold: 68
cooldown_hours: 2                  cooldown_hours: 24
v2_sell_threshold: 55              v2_sell_threshold: 58

# v4 account override:
v2_min_hold_minutes: 0             (no change -- kept as-is)
```

---

## Improvement Plan (Full Reference)

### Tier 1: High-Impact, Low-Risk Changes (config only)

<a id="1-increase-cooldown"></a>
1. **Increase cooldown from 2h to 24h** -- Prevents the profit-take -> rebuy-high -> stop-out cycle. This single change would have saved ~$200-400 across both accounts.

<a id="2-raise-buy-threshold"></a>
2. **Raise buy threshold from 60 to 68** -- Currently 54% of stocks pass. At 68, only ~20-25% would pass, creating real selectivity. Fewer, higher-conviction positions.

<a id="3-widen-hysteresis"></a>
3. **Widen sell threshold hysteresis to 50 (from 55)** -- An 18-point gap (buy at 68, sell at 50) prevents oscillation around the threshold. Stocks need to genuinely deteriorate before being sold.

<a id="4-fix-v4-hold-time"></a>
4. **Fix V4 min_hold_minutes: set to 30 (not 0)** -- Zero hold time causes instant flips that are pure waste.

<a id="5-reduce-purchase-power"></a>
5. **Reduce `purchase_power_pct` from 0.50 to 0.30** -- Fewer, more concentrated positions with meaningful P&L per trade.

### Tier 2: Scoring Model Improvements (code changes)

<a id="6-fix-momentum"></a>
6. **Fix momentum scoring inflation** -- The momentum scorer gives 78/100 on average. It should penalize overextended stocks (RSI>70, price far above moving averages) rather than rewarding pure return. Add mean-reversion signals.

<a id="7-trend-quality"></a>
7. **Add trend-quality filter to technical scoring** -- Currently awards 15 points just for SMA20 > SMA50, even when both are declining. Require rising SMAs, not just crossover.

8. **Improve sentiment beyond keyword matching** -- The keyword-based sentiment (positive/negative word counting) is extremely crude. Consider using Alpaca's built-in news sentiment or an LLM-based approach.

9. **Make fundamental scoring time-aware** -- Fundamental data is cached for 80 days. P/E and P/B computed at runtime are good, but ROE/margins from 80-day-old data may be stale after earnings.

### Tier 3: Architecture Improvements (bigger changes)

<a id="10-re-entry-premium"></a>
10. **Add a "re-entry premium"** -- After selling a ticker at profit, require it to pull back X% from the sell price before re-buying (e.g., won't re-buy until price drops 2% below the sell price). This prevents buying the top.

11. **Position sizing by conviction** -- Instead of equal allocation across 50+ stocks, size positions proportionally to score -- high-conviction picks get 3-5% of portfolio, marginal ones get 1%.

12. **Track realized P&L in `trade_history` table** -- Currently there's no trade_history table, making it hard to analyze performance systematically. Add one that records entry/exit/P&L/hold-time per round-trip.

13. **Add a daily P&L stop** -- If daily realized losses exceed a threshold (e.g., -$200), stop trading for the day to prevent cascade losses.

---

## Biggest Winners & Losers

### V2 Top Winners
- BAC: +$67.76 (profit_take +5.59%)
- C: +$53.82 (profit_take +5.34%)
- COHR: +$48.40 (profit_take +4.01%)
- INTC: +$46.16 (profit_take +3.29%)
- STX: +$45.03 (profit_take +3.33%)

### V2 Top Losers
- WAB: -$66.64 (loss_cut -2.02%)
- Q: -$31.17 (loss_cut -2.05%)
- SLB: -$27.99 (loss_cut -2.75%)
- BKR: -$27.41 (loss_cut -2.83%)
- COHR: -$26.41 (loss_cut -2.22%)

### V4 Top Winners
- GLW: +$88.97 (profit_take +6.52%)
- WDC: +$87.90 (profit_take +5.12%)
- EOG: +$78.48 (profit_take +5.08%)
- CF: +$78.12 (profit_take +5.14%)
- JBL: +$70.85 (profit_take +5.22%)

### V4 Top Losers
- DELL: -$49.88 (loss_cut -4.85%)
- STX: -$46.91 (loss_cut -3.04%)
- VRT: -$44.10 (loss_cut -3.22%)
- DELL: -$43.75 (loss_cut -3.02%)
- WDC: -$43.39 (loss_cut -3.28%)
