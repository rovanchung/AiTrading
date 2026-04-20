# Alpaca Compliance Reference

Rules we care about when trading through Alpaca, and how our code enforces them.

## 1. Account types

Alpaca classifies every account via the `multiplier` field on `GET /v2/account`:

| `multiplier` | Account type            | Borrowing | Settlement rules apply  |
| ------------ | ----------------------- | --------- | ----------------------- |
| `1`          | Cash account            | No        | **Yes — GFV/free-ride** |
| `2`          | Margin (non-PDT)        | Yes, 2×   | No                      |
| `4`          | Margin (PDT, ≥ $25k eq) | Yes, 4×   | No                      |

Supporting indicators:

- `shorting_enabled: true` → margin account
- `pattern_day_trader: true` → PDT flag is active
- `daytrade_count` → rolling 5-business-day count

Quick check across all four accounts:

```bash
~/python_env/torch-env/bin/python3 -c "
from core.config import load_config, activate_version
from executor.alpaca_client import AlpacaClient
for v in ['v1','v2','v3','v4']:
    activate_version(v)
    cfg = load_config(version=v)
    acct = AlpacaClient(cfg).client.get_account()
    m = int(acct.multiplier)
    kind = {1:'CASH', 2:'MARGIN', 4:'MARGIN+PDT'}.get(m, f'UNKNOWN({m})')
    print(f'{v}: {kind}  equity=\${float(acct.equity):,.0f}  PDT={acct.pattern_day_trader}')
"
```

## 2. Margin interest

**Interest is charged once per day on the end-of-day debit balance (negative `cash`).** Intraday
peaks are irrelevant — if you return to positive cash by market close, interest for that day is zero.

```
if cash_at_market_close >= 0:
    interest_for_day = 0
else:
    interest_for_day = abs(cash_at_close) * (annual_rate / 360)
```

Scenarios (assuming starting cash $9,005 — our v3 paper snapshot):

| Scenario                           | Intraday low       | End-of-day cash | Interest           |
| ---------------------------------- | ------------------ | --------------- | ------------------ |
| Buy $5k 10am, sell $5k 3pm         | $4,005             | $9,005          | $0                 |
| Buy $15k 10am, sell $15k 3pm       | −$5,995 (borrowed) | $9,005          | $0 (no EOD debit)  |
| Buy $15k 10am, hold overnight      | −$5,995            | −$5,995         | ~$2/day at 12% APR |
| Buy $15k 10am, sell $10k 3pm       | −$5,995 → $4,005   | $4,005          | $0                 |

Alpaca's live margin rates are tiered by debit size (~7.75% to ~12.75% APR depending on balance).
Paper accounts accrue no interest, so paper backtests under-report the real cost of leveraged
overnight holds.

## 3. PDT rule

**Flag trigger:** 4+ day trades within any rolling 5 business days, provided those day trades are
>6% of total trading activity in that window. A day trade = open + close (or short + cover) the
same security the same day.

**Consequences once flagged:**

- Account must maintain **equity ≥ $25,000** (prior-day close) to day-trade further.
- If equity drops below $25k, the 5th day trade is blocked or triggers an equity-maintenance call.
- Unmet calls → **closing-only** restriction (sells and buy-to-cover only) until cured.
- Alpaca allows **one PDT flag removal per lifetime** via support, for genuine mistakes.

**Relevance to this system:** our 1-minute rebalance cadence combined with cooldown-free
redistribution sells (see `WORKFLOW.md` step 6) can rack up day trades during volatile periods.
The cash guard does *not* prevent PDT — it is an orthogonal concern. Consider a PDT guard if
running live with a sub-$25k account.

## 4. Cash-account settlement violations

**These apply only to cash accounts (`multiplier: 1`)**, not margin accounts. Included here as
justification for why our live accounts should use margin (`multiplier: 2`).

- **Good-Faith Violation (GFV):** buy with unsettled sale proceeds, then sell the new position
  before the original sale settles (T+1 in 2026). Three GFVs in 12 months → 90-day settled-cash-only
  restriction.
- **Free-Ride Violation:** buy a stock and sell it to pay for that same buy (no settled cash to
  cover). Even one → 90-day restriction.
- **Cash-Liquidation Violation:** buy on unsettled funds, then sell a *different* security to
  settle the original buy. Same penalty.

Our 1-minute rebalance cadence is fundamentally incompatible with settlement rules — same-day
round-trips are routine. Running in a cash account would stack violations quickly. Using margin
accounts shifts the risk to PDT (manageable via equity floor) rather than GFV (no workaround
short of changing the strategy).

## 5. How the cash guard enforces "no borrow"

Goal: prevent the program from using margin in normal operation, while keeping margin available
as a safety net when fill-price slippage exceeds our estimate. Interest is therefore zero unless
fills exceed our slippage buffer.

**Config** (`config.yaml`):

```yaml
trading:
  cash_only: true             # master toggle (default)
  cash_slippage_buffer: 0.005 # 0.5% reserve against fill-price slippage
```

**Execution flow** (`orchestrator/pipeline.py → _apply_cash_guard`, called from
`_atomic_evaluate_and_execute` between signal filtering and order submission):

1. Split filtered signals into `sells[]` and `buys[]`.
2. Compute budget:
   ```
   budget = account.cash
          + sum(sell.qty × price × (1 − slippage))    # expected proceeds
          − sum(pending_buy.qty × price × (1 + slippage))  # reservations
   ```
3. Compute aggregate proposed buy cost:
   ```
   buy_cost = sum(buy.qty × price × (1 + slippage))
   ```
4. If `buy_cost ≤ budget`: submit as-is.
5. If `budget ≤ 0`: drop all buys (log warning).
6. Otherwise: scale each buy's qty by `budget / buy_cost` (floor to int). Buys scaled to zero are
   dropped.
7. Submit sells first so fills free cash before buy orders are placed.

**What the guard does not cover:**

- PDT count (see §3). Add a separate guard if needed.
- Race conditions between the cash read and order submission from outside the pipeline's trade
  lock (manual `scripts/*` tools). In practice the lock covers the main pipeline.
- Fill-price slippage beyond `cash_slippage_buffer` (0.5%). When this tail fires, Alpaca extends a
  few dollars of margin to cover — this is the intended safety net.

**Disabling:** set `cash_only: false` in `config.yaml` or in a per-account override block.

## 6. Field reference for `GET /v2/account`

Fields our code reads (`executor/alpaca_client.py:get_account`):

| Field            | Meaning                                                                      |
| ---------------- | ---------------------------------------------------------------------------- |
| `cash`           | Literal cash balance. Goes negative when borrowing. **The no-borrow budget.** |
| `buying_power`   | Reg T buying power including 2× leverage. Read for visibility only — not used for gating; the cash guard sizes buys against `cash` so we never tap this margin headroom. |
| `equity`         | `cash + long_market_value − short_market_value`. Used for PDT $25k test.    |
| `portfolio_value`| Same as `equity` for long-only accounts. Drives our sizing formula.          |
| `status`         | `ACTIVE` normally; other values indicate broker-imposed holds.               |

Fields worth consulting but not currently wrapped:

- `pattern_day_trader`, `daytrade_count` — for a future PDT guard.
- `trading_blocked`, `account_blocked` — for a closing-only restriction guard.
- `multiplier` — to assert live account type at startup.
- `initial_margin`, `maintenance_margin` — to monitor margin-call proximity.
- `non_marginable_buying_power` — cash buying power for non-marginable securities (leveraged ETFs,
  sub-$3 stocks). **Not** the same as `cash`; includes SMA credit.

## 7. References

- Alpaca docs — Pattern Day Trader: https://docs.alpaca.markets/docs/pattern-day-trader
- Alpaca docs — Margin Trading: https://docs.alpaca.markets/docs/margin-trading
- FINRA — Day-Trading Margin Requirements: https://www.finra.org/rules-guidance/key-topics/day-trading
- SEC — Cash Account Trading: https://www.investor.gov/introduction-investing/investing-basics/glossary/cash-account
