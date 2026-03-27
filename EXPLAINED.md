# AiTrading — How It Works (Plain Language)

A walkthrough of the dry run output, explaining every step in terms of math and logic rather than finance jargon.

---

## Step 1: Get the Stock Universe

```
Starting scan on 503 tickers...
```

We pull the S&P 500 list from Wikipedia — 503 companies that are large and well-established (think: Apple, Google, ExxonMobil). This is our search space. We don't look at small/risky companies.

---

## Step 2: Filter Down (503 → 94)

Four sequential filters, each removing stocks that don't meet criteria:

```
After data fetch: 503 tickers with valid data
After price filter: 466          (removed 37)
After volume filter: 459         (removed 7)
After MA filter: 107             (removed 352)
After RS filter: 94              (removed 13)
```

### Price Filter (503 → 466)
Remove stocks priced below $5 or above $500. Below $5 = "penny stocks" (too volatile, unreliable). Above $500 = we want more stocks in play for diversification.

### Volume Filter (466 → 459)
Remove stocks where fewer than 500,000 shares trade per day on average. **Why**: If a stock has low volume, you can't buy/sell quickly without moving the price against yourself. Think of it like a market with few buyers — you get worse prices.

### Moving Average Filter (459 → 107) — the big cut
This is the most important filter. It removes **352 stocks** — everything in a downtrend.

**The math**: For each stock, we compute the **50-day Simple Moving Average (SMA)** — just the average closing price over the last 50 trading days. If today's price is BELOW that average, the stock is trending down. We only keep stocks where price > SMA50.

```
SMA50 = (P₁ + P₂ + ... + P₅₀) / 50

Keep if: Current Price > SMA50
```

**Intuition**: If a stock is below its own 50-day average, it's been falling. We want stocks that are going UP. This single filter eliminates ~70% of stocks because at any given time, many stocks are declining.

### Relative Strength Filter (107 → 94)
Compare each stock's 1-month return to SPY (the S&P 500 index). Remove stocks that are underperforming the market as a whole.

```
Stock return = (Price_today / Price_20_days_ago) - 1
SPY return   = (SPY_today / SPY_20_days_ago) - 1

Keep if: Stock return > SPY return
```

**Intuition**: Even among rising stocks, we only want ones rising FASTER than the overall market. If SPY is down 5.7% this month but a stock is only down 3%, that stock is actually outperforming — it's showing relative strength.

---

## Step 3: Score Each Candidate (0–100)

Each of the 94 surviving stocks gets scored across 4 dimensions. Each dimension produces a score from 0 to 100.

### Example: CF Industries (top pick, composite = 78.4)

```
CF: T=75.0  F=85.0  M=90.0  S=56.0  => C=78.4
```

The **composite score** is a weighted average:

```
Composite = 0.35 × Technical + 0.25 × Fundamental + 0.25 × Momentum + 0.15 × Sentiment
         = 0.35 × 75 + 0.25 × 85 + 0.25 × 90 + 0.15 × 56
         = 26.25 + 21.25 + 22.5 + 8.4
         = 78.4
```

### Technical Score (T) — "Is the price chart healthy?" — weight 35%

Uses mathematical indicators computed from price and volume history:

| Indicator | What it measures | Math | Points |
|-----------|-----------------|------|--------|
| SMA20 > SMA50 | Short-term trend above medium-term | 20-day avg > 50-day avg | +15 |
| SMA50 > SMA200 | Medium above long-term ("golden cross") | 50-day avg > 200-day avg | +15 |
| ADX > 25 | Trend strength (not direction) | Directional movement index | +10 |
| RSI 40–70 | Not overbought or oversold | 14-day relative strength | +15 |
| MACD histogram rising | Momentum accelerating | Difference of EMAs, increasing | +15 |
| Volume above average | Confirms price moves | Today's vol > 20-day avg vol | +10 |
| OBV rising | Money flowing in | Cumulative vol × direction | +10 |
| Bollinger width OK | Not too calm or chaotic | Band width 2–15% of midline | +5 |
| ATR in range | Healthy volatility | True range 1–5% of price | +5 |

**Total possible: 100**

**Key concepts explained:**

- **SMA (Simple Moving Average)**: Just the mean of the last N closing prices. When shorter averages are above longer ones, price is trending up. Think of it as: "recent prices are higher than older prices."

- **RSI (Relative Strength Index)**: Measures the ratio of up-moves to down-moves over 14 days, scaled 0–100. RSI of 70+ = "overbought" (too many buyers, likely to pull back). RSI of 30- = "oversold." We want the sweet spot: 40–70.

  ```
  RSI = 100 - (100 / (1 + avg_gain / avg_loss))
  ```

- **MACD**: Takes two exponential moving averages (12-day and 26-day), subtracts them. When this difference is positive AND increasing, momentum is accelerating upward.

- **ADX**: Measures HOW STRONG a trend is, regardless of direction. ADX > 25 = strong trend. We combine this with other indicators to confirm the trend is UP.

- **OBV (On-Balance Volume)**: Adds volume on up-days, subtracts volume on down-days. If OBV is rising, more volume is happening on up-days — "smart money" is buying.

### Fundamental Score (F) — "Is the company financially solid?" — weight 25%

Uses financial ratios from the company's reports:

| Metric | What it means | Good value | Points |
|--------|--------------|------------|--------|
| P/E ratio | Price / Earnings per share | < 15 (cheap) | +15 |
| PEG ratio | P/E / Growth rate | < 1 (growth at reasonable price) | +10 |
| P/B ratio | Price / Book value | < 3 | +10 |
| ROE | Return on Equity (profit / shareholders' money) | > 15% | +15 |
| Profit margin | Net income / Revenue | > 10% | +10 |
| Revenue growth | Year-over-year sales increase | > 10% | +10 |
| Current ratio | Short-term assets / Short-term debts | > 1.5 | +10 |
| Debt/Equity | Total debt / Shareholders' equity | < 50% | +10 |
| Free cash flow | Cash left after all expenses | > $0 | +10 |

**Total possible: 100**

**Intuition**: A stock can have a great chart (high T) but if the company is drowning in debt or losing money, it's risky. CF scored F=85 because it has strong profitability AND reasonable valuation.

### Momentum Score (M) — "How fast is it rising?" — weight 25%

Pure price performance measurement:

| Metric | What it measures | Points |
|--------|-----------------|--------|
| 1-month return | % gain last 20 trading days | 0–25 |
| 3-month return | % gain last 60 trading days | 0–25 |
| Acceleration | Is 1-month pace faster than 3-month pace? | +15 |
| Consecutive up days | How many of last 10 days were up? | 0–15 |
| Beating SPY (1mo) | Outperforming the market short-term | +10 |
| Beating SPY (3mo) | Outperforming the market medium-term | +10 |

**Total possible: 100**

CF scored M=90 — it has been rising fast, accelerating, and outperforming the market. Several energy stocks (CTRA, COP, OKE, DVN) also scored M=100, meaning very strong recent price gains.

### Sentiment Score (S) — "What does the news say?" — weight 15%

Simplest dimension. Scans recent news headlines for positive/negative keywords:

```
Baseline score = 50 (neutral)
Each positive word (growth, profit, upgrade, strong...): +3
Each negative word (loss, downgrade, lawsuit, weak...):  -3
Final score clamped to 0–100
```

This is the weakest signal (hence only 15% weight). EXE scored S=77 (very positive news), while CTRA scored S=47 (slightly negative). Most stocks land between 47–65.

---

## Step 4: Rank and Select

The top 20 are displayed. In live mode, the system would:

1. **Buy** the top candidates with composite ≥ 65 (there are ~15 qualifying here)
2. **Fill up to 10 positions** (configurable max)
3. **Size each position** based on risk — typically 2% of portfolio risk per trade

### What would be bought (composite ≥ 65):

| # | Stock | Composite | Why it scored high |
|---|-------|-----------|-------------------|
| 1 | CF | 78.4 | Strong across all 4 dimensions |
| 2 | AKAM | 76.2 | Excellent technicals (90) + perfect momentum (100) |
| 3 | EOG | 75.3 | Perfect momentum (100) + strong fundamentals (75) |
| 4 | EXE | 75.0 | Great technicals + fundamentals + very positive news (77) |
| 5 | CTRA | 73.5 | Perfect momentum (100) + strong fundamentals |
| 6 | COP | 73.3 | Balanced strength, perfect momentum |
| 7 | OKE | 73.2 | Perfect momentum + solid fundamentals |
| 8 | DELL | 72.8 | Excellent technicals (90) + near-perfect momentum (95) |
| 9 | ROST | 71.5 | Well-rounded: T=85, F=60, M=70, S=62 |
| 10 | APA | 71.5 | Perfect momentum + solid fundamentals |

Notice a pattern: **energy stocks dominate** (EOG, CTRA, COP, OKE, DVN, APA, HAL, XOM, PSX, VLO). This means the energy sector is currently in a strong uptrend. The system would hit the **30% sector limit** and diversify into other sectors too.

---

## The Decision Logic (What Happens in Live Mode)

### Buying
```
IF composite_score >= 65
   AND technical_score >= 50
   AND open_slots_available (< 10 positions)
   AND sector_limit_ok (< 30% in one sector)
   AND cash_reserve_ok (keep 20% cash)
THEN → BUY
```

### Selling (checked every 30 seconds + every 15 minutes)
```
IF price drops 5% from purchase price     → SELL (stop-loss)
IF price drops 3% from its highest point   → SELL (trailing stop)
IF price rises 15% from purchase price     → SELL (take-profit)
IF composite_score drops below 40          → SELL (score decay)
IF held > 10 days with < 1% gain           → SELL (stagnation)
IF a new stock scores 20+ points higher    → SELL weakest, BUY replacement
```

### Portfolio Protection
```
IF portfolio drops 10% from peak → reduce all positions by 50%
IF portfolio drops 15% from peak → sell EVERYTHING, pause 24 hours
```

---

## Why This Approach Works (Mathematically)

The system combines **trend following** (ride winners) with **risk management** (cut losers fast):

1. **Asymmetric risk/reward**: Stop-loss at -5%, take-profit at +15%. You need to be right only 1 out of every 3 trades to break even. If you're right 40% of the time: expected value = 0.4 × 15% - 0.6 × 5% = +3% per trade.

2. **Trailing stops** let winners run. If a stock goes up 12% then drops 3% from there, you lock in ~9% profit instead of waiting for the 15% take-profit.

3. **Multi-dimensional scoring** avoids single-point-of-failure analysis. A stock with great technicals but terrible fundamentals (like VRSN: T=95, F=30) gets a moderate composite score (70.3) rather than being a top pick.

4. **Continuous re-evaluation** every 15 minutes means the system adapts. If a stock's score drops from 70 to 35, it sells — you don't hold onto losers hoping they recover.

5. **Drawdown protection** prevents catastrophic losses. The -15% liquidation circuit breaker means even in a market crash, your maximum loss is bounded.

---

## Step 5: Macro-Economic Overlay

Before the system picks stocks, it reads the "weather" of the overall economy:

```
=== MACRO ASSESSMENT ===
  Score:  44.9/100
  Regime: neutral
  Cycle:  late_cycle
  VIX:    27.4
  Market breadth: 55%
  SPY vs 200-SMA: -1.9%
```

### What these indicators mean

| Indicator | What it measures | How to read it |
|-----------|-----------------|----------------|
| **VIX** | Market fear level (0-80 scale) | <15 = calm, 20-30 = nervous, >30 = panic. At 27.4, markets are uneasy. |
| **Yield spread** | 10-year minus short-term treasury rate | Positive = economy OK. Negative ("inverted") = recession warning. |
| **Market breadth** | % of sectors above their 200-day average | 55% means slightly more than half of sectors are healthy — mixed signals. |
| **SPY vs 200-SMA** | S&P 500 position vs its long-term average | At -1.9%, the market is just below its 200-day average — borderline bearish. |
| **Rate trend** | Are interest rates rising or falling? | Rising = harder for companies to borrow/grow. Falling = tailwind. |

### How it affects trading

The macro score (44.9) classifies the regime as **neutral** — not great, not terrible. This adjusts the system:

- **Max positions**: 10 → 8 (hold fewer stocks when uncertain)
- **Buy threshold**: stays at 65 (only slightly cautious)
- **Cash reserve**: stays at 20%

The cycle classification (**late_cycle**) adjusts which sectors get more room:
- **Energy, Materials, Industrials, Healthcare** → favored (allowed 40% of portfolio)
- **Tech, Consumer Discretionary** → disfavored (capped at 15%)

This is why the system's energy-heavy picks are appropriate right now — the macro overlay independently confirms that energy is a late-cycle play.

### The math behind the macro score

```
Macro = 0.25 × VIX_score + 0.20 × Yield_score + 0.25 × Breadth_score
      + 0.20 × SPY_trend_score + 0.10 × Rate_score

VIX at 27.4     → score 30  (elevated fear)
Yield at +0.80  → score 60  (healthy but not strong)
Breadth at 55%  → score 55  (mixed)
SPY at -1.9%    → score 46  (slightly below average)
Rates stable    → score 60  (neutral)

= 0.25×30 + 0.20×60 + 0.25×55 + 0.20×46 + 0.10×60
= 7.5 + 12 + 13.75 + 9.2 + 6 = 48.5
```

If the macro score drops below 40 (risk-off), the system gets very defensive:
- Buy threshold jumps to 75 (only the top ~5% of stocks qualify)
- Max positions drop to 5
- Cash reserve increases to 35%
