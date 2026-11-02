# Section 15: Technical Indicators
*(Extracted from video — in video order)*
## 1. What Indicators Are
- Literally "pointers"; in practice, **mathematical calculations applied to price**.
- Price is a number; the market shows a list of numbers (how an asset's price changed over time). Indicators are math formulas, developed over time, used to:
  1. **Understand** past price behavior/character.
  2. **Predict** what price may do next.
- Most are derived from math calculations. Some only help understand price action so far; others also help predict the next move.

### Misconception
- Some people (even some who "teach") say indicators are useless for trading. This is **false**: they've existed a long time, were developed over time, are used at **institutional level**, and are reported in business news.
- With AI, **automated trading systems** (incl. high-frequency) are developing fast; we'll likely explore automation too. Automated systems are objective and rule-based — almost all run math calculations like indicators.
- Even if you never use indicators in your model, **understanding them is crucial** — hence this section.

## 2. Advantages
1. **Makes the trading system objective.**
   - *Objective* = reasoned: every entry/exit has a reason/rule (e.g., "if X, enter; if Y, exit"). *Subjective* = feeling/intuition ("seems right", "I like it today") — can't be replicated; another person given your strategy couldn't repeat it.
   - Problem with subjective: take 10 trades and get a result — no confidence the next 10 trades give a similar result (not replicable). With defined rules, the chance of replicating results is much higher.
   - Indicators are pure math with **no feeling component**; they give defined rules (e.g., if X → buy, if Y → sell). This is why they're used in automated systems.
2. **Trend identification** — trend is sometimes hard to judge by eye (e.g., slow market patterns); many indicators help confirm the current trend.
3. **Momentum and volatility** — **momentum** = strength/power of price's movement; **volatility** = how much price is fluctuating. Indicators express these numerically (like a car's speedometer shows speed).
4. **Signal generation** — some give buy/sell signals. Even with a different trading model/strategy, indicators can serve as **confirmation**/additional input.

## 3. Disadvantages
1. **Lagging** — based on past data (calculated from the existing price list); they react after the move. By the time a buy signal appears, the move may already have started/finished; entering then, price may reverse. Especially problematic on **short timeframes**, where structure doesn't last.
2. **False signals** — indicators respond directly to price: price goes up → buy signal; reverts → immediate sell signal. Confusing signals; you must be able to **filter**.
3. **Over-complication** — can overcomplicate the trading strategy, especially when many indicators are used. *(Sentence cut off in the video.)*

## 4. Five Indicators Covered

### A. Moving Average (MA)
**Formula**
- Takes the **closing prices** of a chosen number of candles (the **period**) and averages them.
- Example (5-candle MA): average the last 5 closes (e.g., 100, 104, 102, …) → one point (e.g., 104). When a new candle forms, average the latest 5 closes again (e.g., new close 112 → average 106) → new point; connect the points → the **moving average line**.
- Everything is editable: source defaults to close (can be changed, e.g., open); period can be 5, 9, 20, 21, 50, 100, 200, etc.

**Types** (differ in the weight given to each candle)
- **SMA (Simple)** — all candles in the period weighted **equally** (sum ÷ period).
- **EMA (Exponential)** — the **latest candles get more weight**, older ones progressively less (exponential).
- Others (e.g., linear-weighted) have their own calculation/constants. Most common: **EMA and SMA**.
- **EMA reacts faster** to price action than SMA (more weight on latest price). Chart example: EMA 20 vs SMA 20 — EMA starts rising/falling first.

**Period effect**
- Fewer candles → hugs the chart, reacts immediately.
- More candles → smoother, slower to respond, further from price.
- Common periods: 9, 20, 21, 50, 200.

**MA Crossover strategy** (buy/sell signals)
- Needs at least **two MAs**: a **short/fast** and a **long/slow** one. Common pairs: **9 & 21** (EMA), also 20 & 50, 50 & 200.
- **Golden Cross (buy/bullish):** fast MA crosses the slow MA **from below to above**.
- **Death Cross (sell/bearish):** fast MA crosses the slow MA **from above to below**.
- Signals work best when **entered with the trend**: effective in a **trending market**. In a **sideways/range market**, crosses happen back and forth immediately → many false signals; sellers get caught, then an instant buy signal appears.
- In a trending market they're far more evident.

**TradingView demo**
- Indicators → search "moving average" → choose type (Simple, Exponential, …). Add Exponential: source = close, length 9. Settings/Style let you change color/thickness. Add a second with length 21 (different color; slow one).
- Blue/yellow lines crossing up = buy; crossing down = sell; chart also shows crossing → immediately re-crossing = false signals.
- **Free-plan limit** on number of indicators per chart: workaround — search for a community-made indicator (searched "three exponential moving average") that creates **three MAs as one indicator**. Disable one in settings and change inputs from defaults (10, 20) to 9 and 21 → counts as one indicator. Deleted afterwards.
- Refinement of this strategy is covered after MACD.

### B. MACD
**Formula/components** (default settings)
- **MACD line**: **12-period EMA − 26-period EMA** (closing prices).
- **Signal line**: **9-period EMA** (of the MACD line).
- **Histogram**: the **difference between the MACD line and the signal line** — describes **momentum** (how much momentum there is).
- Defaults can be edited, but the indicator was built on these defaults.

**Signals**
- MACD line crosses **above** signal line (from below) → **bullish/buy**.
- MACD line crosses **below** signal line (from above) → **bearish/sell**.

**TradingView demo**
- Indicators → "MACD". Appears below the chart: histogram, MACD line (blue), signal line (red).
- Sell: blue crosses down through red; buy: crosses up.
- In sideways/zigzag price, crosses stack up repeatedly → on a live market, entering on a cross gets reversed → **many false signals**; must refine.

### Refining MA Crossover / MACD (trend filter)
Both work only in a **trending market** — confirm it first. Eyeballing a trend isn't enough; add a **200 EMA**:
1. **Slope**: 200 EMA must have a clear slope (up or down). Nearly flat → sideways, not trending.
2. **Position**: bearish trend → price **below** 200 EMA; bullish → price **above** it.
3. **Distance**: price shouldn't be too far from the 200 EMA (momentum may be exhausted; poor time for a sell signal). Wait for price to come **close** to the EMA while the slope is still good.
   - **Sell**: price below 200 EMA, close to it, EMA slope clearly downward.
   - **Buy**: price above 200 EMA, close to it, EMA slope clearly upward.
4. **Confirmation**: after a crossover, wait for **at least one candle to close** on your timeframe (so a cross doesn't immediately reverse).
5. **MACD-specific**: for a **sell**, need a **negative histogram** (good bearish momentum); for a **buy**, a **positive histogram**.
- Chart example: downward slope, price below and near the EMA → wait for MACD cross down; a cross occurred with no good negative histogram (skip); a later cross with a clear negative histogram gave a good sell entry.
- Not perfect every time, but gives a **usable strategy**. Go explore on the market.

### C. RSI (Relative Strength Index)
- A **momentum** indicator; measures the **differences between consecutive closing prices** over **14 periods** (default).
- **Calculation**:
  - Change between closes (e.g., close 100 → next 102 = +2; next 101 = −1).
  - Split into **gains** and **losses**; sum positive changes ÷ 14 = **average gain**; sum of negative changes ÷ 14 = **average loss**.
  - **RS = average gain ÷ average loss**; **RSI = 100 − 100 / (1 + RS)**.
  - Video example: total gains 16, losses 7 over 14 periods → RSI ≈ **69**. *(Subtitle numbers partly garbled.)*
- Plotted in a **separate panel below the chart** as a line oscillating between 0–100, with a middle level at **50**. (In TradingView the yellow line on the RSI is a moving average of the RSI.)
- **70/30 rule** (defaults):
  - **Above 70 = overbought** — people bought a lot, price very expensive/over-extended; may **start falling**.
  - **Below 30 = oversold** — sold more than it should be, price very cheap; may **start rising**.
- Other strategies exist (e.g., **divergence**) — no time; explore yourself.
- Don't sell *only* because RSI > 70 or buy *only* because < 30 — use it as an **input / confirmation signal**.

### D. Bollinger Bands
- A **volatility** indicator — tells how volatile the market is.
- **Calculation** (default 20 period):
  - Takes the last **20 closing prices**; their average = **middle line (moving average)**.
  - Considers the **spread**: how far prices are from the mid line (positive/negative distance).
  - Two more lines are drawn at **±2 standard deviations** from the middle line.
- **Standard deviation** (stats): measures how dispersed the sample data (20 closes) is around the average. At **2 SD**, ~**95%** of the sample data lies between the bands; only ~5% is outside (outliers). So the bands show that ~95% of price data sits between them.
- **Two ways to use for buy/sell**:
  1. **Trending market → Breakout strategy**
     - Price **breaks the upper band** (in an uptrend) → bullish signal; price continues and the market keeps rising.
     - Price **breaks the lower band** (in a downtrend) → bearish/sell signal.
  2. **Sideways/ranging market → opposite (reversal)**
     - Price touches the **lower band** → likely to reverse up → **buy** signal.
     - Price touches the **upper band** → reverse down → **sell** signal.
  - Internalize/backtest these.
- **Volatility reading**:
  - Bands **squeezed/tight** → volatility is very low; price not moving much (sideways market).
  - Bands **expanding/wider** → volatility rising, market starting to move. A consolidating market starting to expand, in a trending market, is used as **additional buy/sell confirmation**.
- **TradingView demo**: Indicators → "Bollinger Bands" → three lines (red = upper, green = lower, middle = SMA of close, default).
  - Trending chart: price crossing upper band → buy (bullish); crossing lower → sell. Price breaks and continues.
  - Sideways chart: price repeatedly touches the lower band and returns up (doesn't break) → buy at reversal; same logic for upper → sell. In a ranging market, price doesn't continue after touching; in a trending market it breaks and keeps going.
  - Bands still lag somewhat.

### E. Fibonacci Retracement
**Where it comes from**
- **Fibonacci sequence**: start 0, 1; each next number = sum of the previous two → 0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233…
- Said that many natural things follow it (e.g., spiral shells, leaf patterns of some trees, etc.). Mathematicians/economists applied it to financial markets: markets don't move straight — price goes up, **pulls back**, goes up again (and the reverse in downtrends). The tool is used to **predict how far a pullback goes** before price continues in the trend.

**Retracement levels (derived from the sequence)**
- **61.8% (Golden Ratio)**: a number ÷ the next number (e.g., 144 ÷ 233 ≈ 0.618); gets closer to 0.618 as numbers grow (early ones like 1/1, 1/2 don't give it).
- **38.2%**: a number ÷ the number two places later (e.g., 89 ÷ 233).
- **23.6%**: a number ÷ the number three places later (e.g., 55 ÷ 233).
- **78.6%**: square root of 0.618.
- **88.6%**: square root of 0.786.
- Plus **0%, 50%, and 100%** (added).
- Full levels: **0, 23.6, 38.2, 50, 61.8, 78.6, 88.6, 100**.

**Usage**
- In a trending market after an impulsive move, price **pulls back**. Fib levels show where the pullback may stop and price resume the trend ("from which level price continues").
- Draw between the **swing low and swing high** (swing = where price turned), following the trend direction:
  - **100%** = price fully retraced back to the start of the move; **0%** = the other extreme end of the move.
  - Uptrend: draw from swing low up to swing high. Downtrend: draw from top down.
- Price retracing to a level (e.g., 61.8%) shows how much pullback occurred.

**TradingView demo**
- Search drawing tools for "Fib retracement". Settings show the levels (0, 0.236, 0.382, 0.5, 0.618, 0.786, 1).
- Levels **above 1** are shown by default — turned off for now (how to use them comes in future strategy sections). Keep just the Fib levels.
- Example 1: impulsive up-leg (swing low → swing high), then pullback; price pulled back to ~**61.8%** and reversed upward — used to predict how far the retracement goes.
- Example 2: another impulsive leg and pullback; price passed the zone, not touching 61.8% exactly. **Doesn't always work perfectly** — this is an intro.
- Downtrend: drawn the same way, top to bottom, following the trend.
