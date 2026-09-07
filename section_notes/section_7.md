# Leverage & Margin
*(extracted from companion video transcript)*

## 1. Setting Up the Concept — Why This Topic Confuses Beginners
- 1 micro lot = 1,000 units. On a standard trading account, holding even 1 micro lot of a currency pair (e.g., EUR/USD) means holding 1,000 EUR worth of position — yet in real CFD trading, a trader can hold that position with ~$100 in their account, and can even hold a full 1 standard lot. **Leverage and Margin explain how this is possible.**

## 2. Real-World Analogy: Buying a House with a Bank Loan
- **Setup**: You have 100,000 Birr in cash. You find a plot of land or house worth 1,000,000 Birr. You believe the market is favorable and want to buy the property, hold it a few months, then resell for profit — but you only have 100,000 Birr, not the full 1,000,000.
- **The bank's offer**: A bank offers to contribute the remaining 900,000 Birr so you can buy the 1,000,000 Birr property. Condition: if you profit when you sell, you pay the bank back their 900,000 Birr. Key point stated explicitly: **the bank's 900,000 Birr can never be "lost" from the bank's perspective** — if the property's value ever fell to 900,000 Birr, **the bank would sell the property itself first** to recover their 900,000, and **your 100,000 Birr would be the part that absorbs the loss instead.**
- **This is stated directly as how Margin works.**

### Profit scenario (worked example)
- You buy the property at 1,000,000 Birr using the bank's help (your 100,000 + bank's 900,000).
- After 6 months, the property's market value rises to 1,500,000 Birr.
- You sell it, return the 900,000 Birr to the bank, and keep the remaining profit.
- Math shown: 1,500,000 − 1,000,000 (original price) = 500,000 Birr profit gained.

### Loss scenario (worked example)
- Suppose instead the property's market value starts **falling** after purchase: 1,000,000 → 950,000 (after a week) → 900,000 (after a month).
- At this point, **even if you don't want to sell**, the bank steps in directly and sells the property itself to recover its 900,000 Birr.
- Result: **your entire 100,000 Birr is wiped out** ("goes into loss") — the bank's money is never at risk; only your contributed portion absorbs the loss.

## 3. Translating the Analogy to Forex Trading

### Key vocabulary mapping, stated explicitly:
- The **900,000 Birr the bank contributed** = analogous to what a broker provides via **Leverage**.
- The **100,000 Birr you personally contributed** = analogous to **Margin** (the amount you personally put toward a trade/position).

### Worked numeric trading example:
- Trader wants to open **1 standard lot**, but their account only has **$100**.
- The broker "tops up" the rest, letting the trader open the position and try to profit.
- If the trade goes against the trader, the broker will close the position once the trader's $100 is used up, and the broker recovers its own contributed funds.

### Terminology: what the 900,000 Birr / broker's contribution is called
- In the house example, the bank's contributed portion is called an **"leverage"**. There, the position you're able to hold is **10 times** what you personally contributed.
- Some brokers allow a leverage **as high as 2000x**.
### Why Leverage matters — both sides stated explicitly:

**The benefit:**
- Leverage allows a trader to hold a **significant/large position**. Example given: with just a $100 account, leverage can allow you to hold a full standard lot or mini lot position— without leverage, a $100 account couldn't open even 1 micro lot.

**The danger:**
- Because leverage lets you hold an oversized position, even a **small price movement** against your position can wipe out your balance **quickly**.
- This is because your account size and the position size become **disproportionate**.
- Explicit conclusion stated: **leverage must be used with discipline and wise risk management**.

## 4. Equity and Balance — Core Definitions

### Balance
- Definition: **the money currently in your account before considering any open (unclosed) trades** — i.e., your deposited funds.
- Example given: if you deposit $100, your Balance = $100.

### Running profit/loss on an open position is "unrealized"
- Example given: after opening a trade (e.g., buying EUR/USD), suppose the position is currently showing $5 profit — but **this is not counted as final/realized profit** while the position remains open, because the price could reverse into a loss at any moment before you close it.
- Terminology stated explicitly: profit/loss on a position that hasn't been closed yet is called **"Unrealized"** profit or loss (as opposed to **"Realized"**, which only applies once the trade is closed).

### Equity
- Definition, stated explicitly: **Equity = Balance + Unrealized (running) profit or loss** across all currently open positions.
- Worked example #1: Balance = $100 deposited, one open position currently showing $5 unrealized profit → **Equity = $105**.
- Worked example #2 (multiple open trades): One trade is at +$5 profit, another trade is at −$10 loss (both still open/unclosed). Net combined = −$5. Balance = $100 → **Equity = $100 − $5 = $95**.
## 5. Margin Level Percentage

### Formula stated explicitly:
**Margin Level % = (Equity ÷ Margin Used) × 100**

### Worked example:
- Account Balance = **$1,000**.
- Account Leverage = **1:100**
- Trader opens a position of **0.1 lot (mini lot)** on EUR/USD = **10,000 units** (EUR).
- Because leverage is 1:100, the trader's own required contribution (Margin) = 10,000 ÷ 100 = **$100**. (Video simplifies EUR/USD conversion as roughly 1:1 for this specific calculation, i.e., treating 100 EUR ≈ $100, "so we don't worry too much about the EUR/USD conversion here.")
- So: **Margin Used = $100** out of the $1,000 account balance.
- Assume the position was *just* opened, so Profit/Loss = $0.00 at this instant → **Equity = Balance = $1,000** (equity and balance are equal right when a trade opens with no price movement yet).
- **Margin Level % = (1,000 ÷ 100) × 100 = 1,000%**

### What Margin Level % tells you:
- Stated explicitly: **the higher your Margin Level %, the safer/healthier your account**.
- Conversely, as this percentage **drops lower and lower**, it signals **increasing losses relative to your account size**, and it becomes more likely the broker will move to close your position(s).
## 6. Margin Call

- **Analogy carried over from the house example**: this is like the moment the bank sends you a warning as the property value approaches the point where their 900,000 Birr contribution is at risk.
- **Definition**: A **notification/warning** the broker sends when your account's Margin Level % drops to a certain threshold, alerting you that your account balance is shrinking and losses are increasing.
- **What it tells you to do**: the notification suggests you either **deposit additional funds** into your account, or **reduce/close some open positions** — to protect your account from further risk.
- **If ignored**: if a trader ignores the Margin Call notification and does not deposit more funds or reduce their positions, and simply continues, they will eventually reach the next stage: **Stop Out**.
- **Why brokers do this**: explicitly explained as the broker protecting its own contributed money (leverage funds). 

### Real broker examples given for Margin Call trigger levels:
- **IC Markets**: at **100%** Margin Level (standard accounts).
- **Exness**: at **60%** Margin Level for standard accounts; for **Professional accounts** at **30%**.
- **Pepperstone**: at **100%** Margin Level.
- **most brokers issue the Margin Call around 100%** Margin Level — I.e when your Equity = Margin you used.

## 7. Stop Out

- **Definition**: The point at which the broker **automatically closes your position(s) itself**, without waiting for your action — to protect the broker's funds, similar to how the bank in the house example would sell the property itself once its value hit 900,000 Birr.
- **Why brokers act before reaching $0 / 0% equity**: because the market can move very fast, and if the broker waited too long, a sudden sharp move could cause **their contributed funds** to be lost as well. 
- **Typical Stop Out thresholds stated**: most brokers trigger Stop Out between **20% and 50%** Margin Level.
- **Exception noted — "Negative Balance Protection" feature**: Exness and IC Markets (named specifically) are brokers that **wait until 0%** Margin Level before stopping out, because they offer a feature called **Negative Balance Protection**. This means if the market suddenly gaps and briefly takes the account into a small negative balance, the broker itself absorbs that loss rather than the trader.
- **General case (without that protection)**: most brokers, lacking this feature, close positions at 20–50% Margin Level — to avoid going negative.

## 8. Summary Recap Given in the Video
- **Leverage** = a loan-like arrangement that allows us to hold a position larger than what we own — but the actual amount that can be *lost* is limited to the contributed margin.

