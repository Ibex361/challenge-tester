# Order Types & Position Sizing

## 1. Position Sizing — CFDs and Standardized Amounts
- There are **three common lot-size tiers**:
-  **Standard lot** ("1 lot" / "standard lot size") = **100,000 units** 
- mini = one tenth of standard, micro = one tenth of mini — establishing the standard/mini/micro relationship.
- Worked example given: if trading EUR/USD and you open a **1 standard lot buy position**, that means you bought **100,000 EUR**.

### Lot sizes differ for non-currency assets
- This 100k/10k/1k unit structure is **specific to currency pairs**.
- For **Gold**: 1 standard lot = **100 ounces**
- For **Oil** (most brokers): 1 standard lot = **1,000 barrels**.
- For **Bitcoin**: 1 standard lot = **1 Bitcoin**.
- Stated explicitly: standardization for currency pairs is nearly universal across brokers, but for other assets (Gold, Oil, cryptocurrencies) the standardized lot size **can vary by broker**

## 2. Pip Value Per Position Size (Worked Example)
examples to show how position size determines the dollar value of 1 pip:

- Pair used: **EUR/USD**
- Starting price: **1.0000**
- Price moves to: **1.0001** (a 1-pip increase — the 4th decimal digit went from 0 to 1)
- Position: **1 standard lot BUY** = 100,000 EUR purchased

**Step-by-step math shown:**
- At entry (1.0000): 100,000 EUR = 100,000 USD (equal value).
- After the move (1.0001): 100,000 EUR × 1.0001 = **100,010 USD**.
- Difference: 100,010 − 100,000 = **$10**.
- **Conclusion stated: 1 pip on 1 standard lot = $10 value.**

Extending this to smaller lot sizes (same logic, stated directly):
- **1 mini lot (0.1 lot) = 10,000 units → 1 pip = $1**
- **1 micro lot (0.01 lot) = 1,000 units → 1 pip = $0.10** ("ten cents")

### Important caveat: this $10/$1/$0.10 rule applies specifically when...
1. The pair's **pip value naturally computes in USD** (i.e., the math works out to a USD directly), **and**
2. The **quote currency is USD**.

- For pairs where the **quote currency is something else** — example given: **USD/CAD** (quote currency = Canadian Dollar, not USD) — the pip value in USD does **not** come out to exactly $10 per standard lot. Because pip value is being measured in USD terms, but the pair's quote currency isn't USD, you have to **convert back to USD** at the end.

## 3. Order Types — Two Major Categories

### A. Market Order
- Example given: if trading EUR/USD and trade execute at current price.

### B. Pending Order
- Definition: "if price reaches X, buy" or "if price reaches Y, sell."
- Types:
  1. **Limit Order**
  2. **Stop Order**
  3. **Stop-Limit Order** (a newer type)

#### i. Limit Order (two variants)
- **Sell Limit**: price is rising, and you expect it to reverse downward once it hits a certain level — you place an order to **sell** once price reaches that (higher) point, expecting a bounce back down.
- **Buy Limit**: price is falling, and you expect it to reverse upward once it hits a certain (lower) level — you place an order to **buy** at that point, expecting the price to bounce back up.
- Core idea: Limit orders are placed anticipating a **bounce/reversal** at a specific point.
- **Execution mechanics**: if price jumps past your limit level without ever touching it exactly (worked example: Sell Limit set at **1.14**, but price jumps from **1.13** straight to **1.15**, skipping 1.14 entirely), the order becomes a **market order** and fills at the **next available price** — not exactly at your specified level. This mainly matters in illiquid/highly volatile conditions; in liquid markets, execution usually does happen at (or very near) the requested price.

#### ii. Stop Order (two variants)
- Analogy given (cars, in Ethiopia): people tend to buy a car once its price starts rising (assuming it'll keep rising) and sell once it starts falling (before it drops further) — Stop orders follow this same directional-momentum logic.
- **Key distinction from Limit orders**: Limit orders anticipate a **bounce/reversal**; Stop orders anticipate **continuation** in the same direction as the current move.
-  Worked example given: an order set at 1.14; price jumps from 1.13 to 1.17, skipping 1.15 and 1.16 entirely — because those prices were never available, execution happens wherever the market actually lands after touching/crossing that zone.
- Noted: this jump/gap scenario is uncommon, especially for liquid major pairs — it happens more with illiquid assets or around volatile times (market open/close, big news events), and is particularly relevant for stocks/indices around their open/close times.

#### iii. Stop-Limit Order (newer order type)
- Described as a **combination** of Stop and Limit orders.
- Stated to be a **newer** feature — **not available in older MetaTrader (MT4)**, only available in **MT5**.
- **Best used for highly volatile markets** — where price can jump/gap significantly and move very fast — as a way to **protect yourself** from being filled at an undesired price.
- **How it works**: you set two price points —
  1. A **"Trigger"** point (functions like a Stop order — direction-following: Sell trigger direction = downward, Buy trigger direction = upward). This is NOT a bounce-back point.
  2. A **"Limit"** point placed after the trigger.
- Your order **only fills** if the market price actually **falls within the range** between the Trigger and the Limit — i.e., between where the stop triggers and where the limit is set. If the market **jumps clean over** that whole range (skips both points, landing beyond the limit), the order is **never filled** at all — which protects you from entering at an unwanted price during an extreme, fast-moving jump.
- Particularly useful for: **stocks and indices**, which are especially prone to big gaps/jumps around market open and close times, and for volatile currency pairs.

## 4. Other Common Order Types: Stop Loss & Take Profit
Stated as separate, simple/easy order types tied to an open position, that the broker asks you to set when opening a trade.

### Stop Loss
- Definition: a price level you set **below your entry** (for a buy) where, if the market moves against your position beyond that point, your broker **automatically closes the trade at a loss** — preventing further losses.
- Purpose: to avoid letting a losing trade run indefinitely — you decide in advance "if it goes this far against me, cut the loss here."

### Take Profit
- Definition: a price level you set where, if the market moves favorably and reaches that point, your broker **automatically closes the trade at a profit**.
- Purpose: locks in gains — protects against a profitable trade reversing back into a loss before you manually close it.
- Both Stop Loss and Take Profit are typically set **at the same time you open the position**.

### Worked numeric example given:
- Entry: **EUR/USD Buy** at **1.008**
- **Take Profit** set at **1.012** → if price rises to 1.012, position closes automatically in profit.
- **Stop Loss** set at **1.006** → if price falls to 1.006 instead, position closes automatically at a loss.
