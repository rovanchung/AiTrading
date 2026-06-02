# AiTrading — Detailed Workflow

Complete execution flow from launch through every scheduled job and trading decision.

## Continuous Mode (`python main.py`)

### 1. Initialization (`main.py`)

1. Parse CLI args, load `config.yaml` via `core/config.py`
2. Set up logging (main log + transaction log) via `core/logging_config.py`
3. Open the per-version SQLite database (`data/trading_{version}.db`, selected by `--version`), initialize schema
4. Create components:
   - `AlpacaClient` — broker connection (Alpaca API)
   - `OrderManager` — order submission with retries
   - `AlertManager` — notification hooks
5. Create `TradingPipeline` which internally creates:
   - `StockScreener` — universe fetch + filter chain
   - `StockAnalyzer` — multi-dimensional scoring engine
   - `PortfolioManager` — buy/sell decision logic
   - `MacroAnalyzer` — economic regime assessment
6. Create `TradingScheduler` which also creates:
   - `PositionMonitor` — real-time stop-loss/take-profit checker (shares trade lock with pipeline)

### 2. Immediate Pre-Market Prep

On startup, the scheduler runs pre-market prep once immediately (same as step 3A below). This ensures a shortlist exists even if the system starts mid-day.

### 3. Scheduled Jobs (APScheduler, all US/Eastern)

**API Provider Legend** (arrows → indicate fallback chains):
- **Alpaca** — broker: market clock, account, positions, orders, OHLCV bars, news
- **Finnhub** — primary fundamentals (EPS, BVPS, ROE, margins, growth)
- **FMP** — fundamentals fallback (ROE, margins, debt, cash flow)
- **yfinance** — last-resort fallback for OHLCV, fundamentals, and news
- **Wikipedia** — S&P 500 constituent list (HTML scrape, cached 7 days)

**Rate Limits & Throttling:**

| Provider | Limit | Enforcement in code | Location |
|----------|-------|-------------------|----------|
| Alpaca | 200 req/min (free tier) | Symbols fetched in 25-ticker chunks (`CHUNK_SIZE`) with per-ticker retry for any silently-dropped symbols | `core/alpaca_data.py` |
| Finnhub | 60 calls/min | Sliding window, capped at 55/min; sleeps when full | `core/finnhub_data.py:50` |
| FMP | 250 req/day (free tier) | 24-hour disk cache (`data/fmp_cache.json`); only calls on miss | `core/fmp_data.py:56` |
| yfinance | ~2 req/s (unofficial) | 0.5s min delay between calls + 1–2s retry backoff | `core/data_provider.py:22` |
| Wikipedia | None | Cached 7 days in DB | `screener/universe.py:36` |

**Caching layers that reduce API calls:**
- Fundamentals cached 80 days in `fundamentals` DB table (`analyzer/fundamental.py:15`)
- FMP results cached 24 hours on disk (`data/fmp_cache.json`)
- Macro assessment cached 4 hours in memory (`analyzer/economic.py:55`)
- Universe (S&P 500 list) cached 7 days in DB

---

#### A. Pre-Market Prep — 9:25 AM Mon-Fri

**Purpose:** Build the day's first shortlist before market open.

**Step A1: Universe Refresh** (`screener/universe.py`)
- Check if cached S&P 500 list is older than 7 days
- If stale, fetch ~503 tickers from Wikipedia's S&P 500 page
- Store in `universe` DB table (ticker, name, sector)
- **APIs:** Wikipedia (HTML scrape) — **0–1 call** (0 if cache fresh)

**Step A2: Macro Assessment** (`analyzer/economic.py`)
- Fetch macro indicators via yfinance:
  - **VIX** (volatility index)
  - **Yield spread** (10Y minus 2Y Treasury)
  - **Market breadth** (% of S&P 500 above 200-day SMA)
  - **SPY vs 200-SMA** (distance percentage)
- Compute macro score (0–100), classify regime (risk-on / neutral / risk-off)
- Determine business cycle phase (expansion / peak / contraction / recovery)
- Calculate parameter adjustments:
  - Buy threshold offset (raise in risk-off, lower in risk-on)
  - Max positions offset (reduce in risk-off)
  - Cash reserve addition (increase in risk-off)
  - Per-sector limits based on cycle phase (favor/disfavor sectors)
- Pass adjustments to `PortfolioManager`
- **APIs:** Alpaca → yfinance — **5 calls:** ^VIX (1), ^TNX (1), ^IRX (1), 11 sector ETFs (1 batch), SPY (1). Index tickers (^) always fall through to yfinance.

**Step A3: Screen** (`screener/screener.py`)
- Batch download 3-month OHLCV data for all ~503 tickers via yfinance
- Apply filter chain sequentially:
  1. **Price filter** — latest close between $5 and $500
  2. **Volume filter** — 20-day average volume > 500,000
  3. **Moving average filter** — price above key moving averages (uptrend)
  4. **Relative strength filter** — outperforming SPY over recent period
- Output: N candidates (varies by market conditions)
- **APIs:** Alpaca → yfinance — **2 calls:** ~503 tickers (1 batch), SPY (1)

**Step A4: Analyze** (`analyzer/analyzer.py`)

For each candidate, compute 4 sub-scores (each 0–100):

| Dimension | Weight | Source | What it measures | API providers |
|-----------|--------|--------|-----------------|---------------|
| Technical | 35% | `analyzer/technical.py` | RSI, MACD, Bollinger Bands, volume trend, moving average alignment | *(uses OHLCV already fetched)* |
| Fundamental | 25% | `analyzer/fundamental.py` | ROE, margins, growth, P/E, P/B from 19 metrics (cached 80 days) | **Finnhub → FMP → yfinance** |
| Momentum | 25% | `analyzer/momentum.py` | Multi-period returns (1w, 1m, 3m), relative strength vs SPY | *(uses OHLCV already fetched)* |
| Sentiment | 15% | `analyzer/sentiment.py` | News headlines, positive/negative keyword scoring | **Alpaca → yfinance** (news) |

- Compute weighted composite score
- Save all scores + details to `scores` DB table
- Sort candidates by composite score descending
- **Per ticker:** Finnhub 0–1 (usually 0, cached 80 days) + Alpaca news 1 = **~1 call/ticker**
- **Step total (N candidates):** Alpaca news ~N, Finnhub 0–N (mostly 0)

**Step A5: Cache Shortlist**
- Take top 50 scored tickers + all currently held tickers
- Store as shortlist for fast intra-day re-ranking
- **APIs:** none (local only)

**Cycle A total:** ~N+7 API calls typical (N candidates × 1 news + 5 macro + 2 OHLCV batches)

---

#### B. Macro Refresh — Every 4 Hours during market hours, Mon-Fri

**Purpose:** Keep the macro overlay current. This **replaced** the old hourly full-universe re-scan — the shortlist is now built once at pre-market prep (Cycle A) and the 1-minute rebalance cycle (Cycle C) does all trading. Cadence is `schedule.macro_refresh_interval_hours` (default 4); with a 9:30 open / 16:00 close it fires at **10:00 and 14:00 ET**.

- Re-assess macro (same indicators and outputs as Step A2) and push adjustments to `PortfolioManager`
- The assessment self-caches (4h TTL), so a fire landing inside a live cache is a cheap no-op
- **APIs:** **0 calls** (cache fresh) or **5 calls** (^VIX, ^TNX, ^IRX, 11 sector ETFs batch, SPY — same as Step A2)

**Cycle B total:** 0–5 API calls, ~2×/day.

> The full universe screen → analyze → evaluate → execute pipeline (`run_full_cycle`) is **no longer scheduled**. It still runs for `--once` and as the bootstrap fallback in Cycle C when no shortlist is cached yet.

---

#### Shared Trade-Decision Block — Atomic Evaluate + Execute (holds trade lock)

Run by Cycle C every minute (and by `run_full_cycle` for `--once` / the no-shortlist fallback). The trade lock prevents concurrent access between cycles.

1. **Sync pending orders** (`orchestrator/pipeline.py → _sync_pending_orders`):
   - Check all DB pending buy orders against Alpaca status
   - If filled: create `Position` record in DB — **unless the ticker is on cooldown** (see *Cooldown enforcement* below). A buy submitted before the name was exited can fill *after* a profit/loss sell cooled it; that stray fill is flattened immediately (`_flatten_cooldown_fill`) instead of re-opening the position.
   - If canceled/expired/rejected: mark order as canceled in DB
   - **Pending-sell fills correct the recorded exit price** (`_reconcile_pending_sells` → `_reconcile_exit_fill`): a full sell closes the DB position at submit time using the stale OHLCV bar close (the market order hasn't filled yet — see Step 8). When the real fill price lands here, it is re-linked to the position it closed (matched by ticker + qty + exit_time ≈ the order's `submitted_at`) and `exit_price`/`pnl` are rewritten in both `positions` and `trade_history` (`db.correct_exit_price`). Without this, a stale bar (e.g. an overnight gap not yet in the bars) leaves a recorded P&L that disagrees with both the real fill and the exit-reason `(%)`.
   - Return map of currently open orders on Alpaca

1b. **Keep pending orders (no blanket cancel):**
   - Orders are **market** orders, so they normally fill within the cycle and rarely survive to the next one. Any that do are left in place rather than cancelled.
   - Protective exit sells (`loss_cut*` / `profit_take*` / `no_longer_qualifies`) must be allowed to fill — cancelling them was what let the same shares churn into repeated phantom loss-cuts.
   - An in-flight redistribution order is "counted" by the same-side pending skip in the execute step (the ticker isn't re-submitted while its order is open); any target-qty drift self-corrects once the order fills.

2. **Get account state** from Alpaca (portfolio value, cash) — **API: Alpaca** (account)
3. **Get live positions** from Alpaca (with avg_entry cost basis) — **API: Alpaca** (positions)
3b. **Reconcile positions to Alpaca** (`core/database.py → reconcile_positions`):
   - Insert open rows for tickers Alpaca holds but the DB doesn't; update drifted qty/cost; close orphans Alpaca no longer holds
   - **Cooldown drift guard:** an untracked Alpaca holding for a name still on cooldown is an unwanted re-entry (a position can only become untracked-and-cooled by being re-bought after a profit/loss sell). It is **not** tracked; its ticker is returned and the pipeline flattens it (`_flatten_cooldown_fill`).
   - **Pending-sell guard:** a name whose exit sell is still in flight is left alone — neither re-tracked nor flattened — so the order can complete. Alpaca keeps showing the shares until the market sell fills, but the DB row was already closed when the exit was issued; re-tracking it would reset `entry_time` and re-arm the loss-cut that triggered the exit. Checked **before** the cooldown guard, and applied on the `sync_local_state` drift path too.
4. **Save portfolio snapshot** to DB (value, cash, invested, peak)

5. **Step 1 — Profit-based sells** (`portfolio/manager.py → _profit_based_sells`):
   - For each held position, calculate P&L using Alpaca `avg_entry` (cost basis)
   - Skip positions held less than `v2_min_hold_minutes` (v2; default 30, but v3/v4 set 0)
   - **Profit take:** sell if P&L >= `profit_take_pct` (config, default +1%)
   - **Loss cut:** sell if P&L <= `-loss_cut_pct` (config, default -0.5%)
   - Sold tickers enter the `cooldown_hours` window (config; currently 8h) — see *Cooldown enforcement* below

6. **Step 2 — Score-based redistribution** (`portfolio/manager.py → _redistribute`):
   - Filter scored candidates to those with composite >= macro-adjusted `buy_threshold`
   - Exclude tickers in cooldown (cooldown is subtracted explicitly, not only via the already-handled set)
   - Calculate proportional allocation: `target_pct = score / total_qualifying_scores`
   - Available capital = `purchase_power_pct` (config, default 50%) × portfolio value
   - For each qualifying stock: compute target qty → sell excess or buy deficit
   - Sell positions that no longer qualify (no cooldown for redistribution sells)

**Cooldown enforcement** (prevents the loss-cut → rebuy loop):
   - A name sold via `profit_take*` / `loss_cut*` is on cooldown for `cooldown_hours` (`db.get_recently_profit_sold`).
   - The cooldown is enforced at **three** points, because a position can be (re)opened by more than just new signals:
     1. **Signal generation** (`_redistribute`) — cooled-down names aren't proposed for buying.
     2. **Pending-buy fills** (`_reconcile_pending_buys`) — a buy placed pre-exit that fills post-exit is flattened, not tracked.
     3. **Alpaca drift reconcile** (`reconcile_positions` / `sync_local_state`) — a stray untracked holding of a cooled-down name is flattened, not tracked.
   - Why three: a buy can fill after the name was already exited and cooled (an order that didn't fill the same cycle, or external drift). Gating only signal generation (the historical behavior) let those late fills silently re-open the position and spin a rapid sell→rebuy loop. Flattening at the open paths closes that gap.
   - **Separate from cooldown**, the *pending-sell guard* (above) stops the open paths re-tracking a name whose own exit sell is still in flight — that, not a re-buy, was the source of the repeated same-entry/same-exit phantom loss-cuts.

7. **Cash guard** (`orchestrator/pipeline.py → _apply_cash_guard`, when `cash_only: true`):
   - Budget = `account.cash` + estimated sell proceeds − value reserved by pending buys (each adjusted by `cash_slippage_buffer`)
   - If aggregate buy cost > budget: scale each buy's qty proportionally; buys scaled to zero are dropped
   - Sells execute first so fills free cash before any buy submits
   - See [ALPACA_COMPLIANCE.md](ALPACA_COMPLIANCE.md) for the broker rules this enforces

8. **Execute signals** (`orchestrator/pipeline.py → _execute_signals`):
   - For each signal, get current price from OHLCV data
   - Submit order via `OrderManager` → **API: Alpaca** (order submission)
   - On buy fill: create `Position` record in DB, log transaction
   - On buy accepted (not yet filled): position created later by sync step on next cycle
   - On full sell: close position in DB, compute P&L, log transaction. The market sell rarely fills this same instant, so `exit_price` is recorded provisionally from the OHLCV bar close (`order.filled_price or current_price`); the real fill price is reconciled in by Step 1 on a later cycle.
   - On partial sell (redistribution): reduce position qty in DB, log transaction
   - On sell failure (no position on Alpaca): close stale DB position, continue
   - On other failure: trigger alert, continue to next signal

8. **Retry logic:** If the cycle fails (network error, API timeout, etc.), retry every 30 seconds until a 12-minute deadline is reached.

**Trade-decision subtotal:** Alpaca **2 + T** (1 account + 1 positions + T orders).

---

#### C. Unified Rebalance Cycle — Every 1 Minute, 9:30 AM–3:59 PM Mon-Fri

**Purpose:** Fast portfolio rebalancing using cached shortlist (~80 tickers). Combines position monitoring and re-ranking into a single cycle with profit-based sells and score-proportional redistribution.

Interval is set by `schedule.rerank_interval_minutes` (default 10, currently 1).

1. If no shortlist cached yet, run full cycle instead (if market open)
2. Re-fetch OHLCV data for shortlist only (~80 tickers) — **APIs:** Alpaca → yfinance — **2 calls** (1 batch OHLCV + 1 SPY)
3. Re-score all shortlist tickers — **APIs:** Alpaca news **~80 calls** (1/ticker), Finnhub **~0** (cached 80 days)
4. Atomic evaluate + execute (the shared trade-decision block above) — **APIs:** Alpaca **2+T calls** (1 account + 1 positions + T orders)

**Per cycle:** ~84+T API calls typical (2 OHLCV + ~80 news + 2 account/positions + T orders). Runs ~390 times/day.

---

### 4. Shutdown

- User presses `Ctrl+C` (or sends SIGINT)
- APScheduler stops (in-progress jobs may finish)
- Database connection closes
- Log: "AiTrading shut down."

---

## Single-Run Modes

### `--once` (one full cycle)

Runs the full pipeline once and exits:
1. Initialization (same as continuous)
2. Pre-market prep (universe + macro + screen + score)
3. Full trading cycle with execution (`run_full_cycle` → the shared trade-decision block)
4. Shutdown

### `--dry-run` (analysis only)

Runs analysis without trading:
1. Initialization (same as continuous)
2. Pre-market prep (universe + macro + screen + score)
3. Print macro assessment (score, regime, cycle phase, indicators, adjusted parameters)
4. Print top 20 candidates with all sub-scores
5. Print count of qualifying stocks (composite >= threshold AND technical >= 50)
6. No orders executed — exit

### `--dashboard` (web UI only)

Launches read-only monitoring dashboard:
1. Load config (database path only)
2. Create Flask app (`dashboard/app.py`)
3. Serve on `http://127.0.0.1:5000`
4. No trading components initialized

---

## Timing Summary (Trading Day)

```
 9:25 AM  Pre-market prep (universe + macro + screen + analyze + cache shortlist)
 9:30 AM  Market open
 9:31     Rebalance cycle (re-score shortlist + profit sells + redistribution)
   ...    (every 1 min, all day — does all trading)
10:00 AM  Macro refresh (push updated overlay to PortfolioManager)
   ...    (rebalance continues every 1 min)
 2:00 PM  Macro refresh
   ...
 3:59     Last rebalance cycle
 4:00 PM  Market close
```

### Daily API Call Estimates (typical trading day)

Assumes ~100 candidates pass screening, ~80 shortlist, ~10 trades/day. Rebalance interval is configurable (`schedule.rerank_interval_minutes`, currently 1 min).

| Cycle | Frequency | Alpaca calls | Finnhub calls | yfinance calls | Total |
|-------|-----------|-------------|---------------|----------------|-------|
| A. Pre-Market Prep | 1×/day | ~102 (2 OHLCV + ~100 news) | ~0 (cached) | ~5 (index tickers) | ~107 |
| B. Macro Refresh | ~2×/day | 0 | 0 | 0–5 (only if cache expired) | 0–10 |
| C. Rebalance | ~390×/day (1 min) | ~32,760 (390 × [2 OHLCV + ~80 news + 2 acct/pos]) | ~0 | ~0 | ~32,760 |
| **Daily total** | | **~32,862** | **~0** | **~10–15** | **~32,877** |

Notes:
- Alpaca free tier allows 200 req/min (~288,000/day) — daily usage is well within limits
- Finnhub calls are near zero on a typical day because fundamentals are cached 80 days
- yfinance calls are only for index tickers (^VIX, ^TNX, ^IRX) that Alpaca doesn't support
- FMP calls are near zero (only on Finnhub failure + FMP cache miss)
- Trade orders add a small variable amount (T calls per cycle, ~10/day total)
