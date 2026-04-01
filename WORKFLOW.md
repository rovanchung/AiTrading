# AiTrading — Detailed Workflow

Complete execution flow from launch through every scheduled job and trading decision.

## Continuous Mode (`python main.py`)

### 1. Initialization (`main.py`)

1. Parse CLI args, load `config.yaml` via `core/config.py`
2. Set up logging (main log + transaction log) via `core/logging_config.py`
3. Open SQLite database (`data/trading.db`), initialize schema
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
| Alpaca | 200 req/min (free tier) | No explicit throttle — batch calls keep volume low | `core/alpaca_data.py` |
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

#### B. Full Trading Cycle — Hourly at 10:00 AM through 3:00 PM, Mon-Fri

**Purpose:** Full universe re-scan + trade execution. Runs 6 times per day.

**Step B1: Market Check**
- Query Alpaca API: is market open?
- If closed, skip entire cycle
- **APIs:** Alpaca — **1 call** (market clock)

**Step B2: Screen** — same as step A3 (full ~503 universe, fresh filters)
- **APIs:** Alpaca → yfinance — **2 calls** (1 batch OHLCV + 1 SPY)

**Step B3: Analyze** — same as step A4 (all passing candidates)
- **APIs:** Alpaca news ~N, Finnhub 0–N (usually 0, cached 80 days)

**Step B4: Macro Refresh**
- Re-assess macro only if cached assessment has expired (4-hour TTL)
- Update portfolio manager adjustments if changed
- **APIs:** **0 calls** (cache fresh) or **5 calls** (if expired, same as step A2)

**Step B5: Update Shortlist** — same as step A5 (no API calls)

**Step B6: Atomic Evaluate + Execute** (holds trade lock)

This is the core trading decision block. The trade lock prevents concurrent access between cycles.

1. **Sync pending orders** (`orchestrator/pipeline.py → _sync_pending_orders`):
   - Check all DB pending buy orders against Alpaca status
   - If filled: create `Position` record in DB
   - If canceled/expired/rejected: mark order as canceled in DB
   - Return map of currently open orders on Alpaca

2. **Get account state** from Alpaca (portfolio value, cash) — **API: Alpaca** (account)
3. **Get live positions** from Alpaca (with avg_entry cost basis) — **API: Alpaca** (positions)
4. **Save portfolio snapshot** to DB (value, cash, invested, peak)

5. **Step 1 — Profit-based sells** (`portfolio/manager.py → _profit_based_sells`):
   - For each held position, calculate P&L using Alpaca `avg_entry` (cost basis)
   - **Profit take:** sell if P&L >= `profit_take_pct` (config, default +1%)
   - **Loss cut:** sell if P&L <= `-loss_cut_pct` (config, default -0.5%)
   - Sold tickers get `cooldown_hours` (config, default 2 hours) before re-buying

6. **Step 2 — Score-based redistribution** (`portfolio/manager.py → _redistribute`):
   - Filter scored candidates to those with composite >= macro-adjusted `buy_threshold`
   - Exclude tickers in cooldown (from profit/loss sells within last 2 hours)
   - Calculate proportional allocation: `target_pct = score / total_qualifying_scores`
   - Available capital = `purchase_power_pct` (config, default 50%) × portfolio value
   - For each qualifying stock: compute target qty → sell excess or buy deficit
   - Sell positions that no longer qualify (no cooldown for redistribution sells)

7. **Execute signals** (`orchestrator/pipeline.py → _execute_signals`):
   - For each signal, get current price from OHLCV data
   - Submit order via `OrderManager` → **API: Alpaca** (order submission)
   - On buy fill: create `Position` record in DB, log transaction
   - On buy accepted (not yet filled): position created later by sync step on next cycle
   - On full sell: close position in DB, compute P&L, log transaction
   - On partial sell (redistribution): reduce position qty in DB, log transaction
   - On sell failure (no position on Alpaca): close stale DB position, continue
   - On other failure: trigger alert, continue to next signal

8. **Retry logic:** If the cycle fails (network error, API timeout, etc.), retry every 30 seconds until a 12-minute deadline is reached.

**Step B6 subtotal:** Alpaca **2 + T orders** (1 account + 1 positions + T orders)

**Cycle B total:** ~N+5+T API calls typical (1 clock + 2 OHLCV + N news + 1 account + 1 positions + T orders; macro 0 if cached)

---

#### C. Unified Rebalance Cycle — Every 1 Minute, 9:30 AM–3:59 PM Mon-Fri

**Purpose:** Fast portfolio rebalancing using cached shortlist (~80 tickers). Combines position monitoring and re-ranking into a single cycle with profit-based sells and score-proportional redistribution.

Interval is set by `schedule.rerank_interval_minutes` (default 10, currently 1).

1. If no shortlist cached yet, run full cycle instead (if market open)
2. Re-fetch OHLCV data for shortlist only (~80 tickers) — **APIs:** Alpaca → yfinance — **2 calls** (1 batch OHLCV + 1 SPY)
3. Re-score all shortlist tickers — **APIs:** Alpaca news **~80 calls** (1/ticker), Finnhub **~0** (cached 80 days)
4. Atomic evaluate + execute (same as step B6) — **APIs:** Alpaca **2+T calls** (1 account + 1 positions + T orders)

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
3. Full trading cycle with execution (step B6)
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
 9:31     Rebalance cycle (profit check + redistribution)
   ...    (every 1 min)
 9:59     Rebalance cycle
10:00 AM  Full trading cycle (full universe re-scan + rebalance)
10:01     Rebalance cycle
   ...    (full cycles hourly, rebalance every 1 min between)
 3:00 PM  Last full trading cycle
   ...
 3:59     Last rebalance cycle
 4:00 PM  Market close
```

### Daily API Call Estimates (typical trading day)

Assumes ~100 candidates pass screening, ~80 shortlist, ~10 trades/day. Rebalance interval is configurable (`schedule.rerank_interval_minutes`, currently 1 min).

| Cycle | Frequency | Alpaca calls | Finnhub calls | yfinance calls | Total |
|-------|-----------|-------------|---------------|----------------|-------|
| A. Pre-Market Prep | 1×/day | ~102 (2 OHLCV + ~100 news) | ~0 (cached) | ~5 (index tickers) | ~107 |
| B. Full Trading | 6×/day | ~624 (6 × [1 clock + 2 OHLCV + ~100 news + 2 acct/pos]) | ~0 | 0–5 (macro if expired) | ~629 |
| C. Rebalance | ~390×/day (1 min) | ~32,760 (390 × [2 OHLCV + ~80 news + 2 acct/pos]) | ~0 | ~0 | ~32,760 |
| **Daily total** | | **~33,486** | **~0** | **~5–10** | **~33,496** |

Notes:
- Alpaca free tier allows 200 req/min (~288,000/day) — daily usage is well within limits
- Finnhub calls are near zero on a typical day because fundamentals are cached 80 days
- yfinance calls are only for index tickers (^VIX, ^TNX, ^IRX) that Alpaca doesn't support
- FMP calls are near zero (only on Finnhub failure + FMP cache miss)
- Trade orders add a small variable amount (T calls per cycle, ~10/day total)
