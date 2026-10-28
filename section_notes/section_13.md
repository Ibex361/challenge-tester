# BirrForex Academy — Section 13: Charts & Timeframes 
*(Extracted in order from the video transcript)*

## 0. Intro
- The previous section started **market analysis** (what it is and its types). This one covers the **visual representation of data**:
## 1. Why Visual Representation?
- Definition: representing the price of an asset visually, in a form that is easy to see and understand.
- An asset's price is a number (a birr or dollar amount), and it changes a lot (e.g., 10 birr today, 13 tomorrow).
- A long list of numbers is hard to read. It is hard to extract a pattern from it and identify what could happen tomorrow.
- So the price is represented in a way that is easy on the eye. Different visual representations of price data exist across different time spans.
- If price changed only once a day (e.g., 1 USD sold at 13 birr today and 12 tomorrow), a list would be fine unless there were many days of data. But when price changes every second and the market is very active, it must be expressed visually.

## 2. Data Categorization — Ticks
Before charting, the data is categorized, starting with the **tick**.

### Tick
- A tick is the **current price** of an asset. When the price changes, the tick value updates immediately, so **each price change is one tick**.
- Example: an asset is 10 birr, a second later 12 birr (another tick), then 13 birr (another tick).
- The time it takes for a tick to form is **not constant**:
  - In very liquid assets, such as the normal Forex market, a tick can form **2–3 times per second**.
  - In very active market periods there can be very many ticks per second.
  - In inactive periods, ticks are spread further apart.
  - In illiquid assets (not traded much), the time between ticks widens.
- A tick is **not** "one per second". It is each price change. In volatile conditions there are very many; in a stable, slow market the gaps can be longer.
- For the CFD instruments we trade, ticks are **very numerous**. Example: a broker such as **Exness** can give you today's gold tick data, which is a very large file (CSV/Excel) with thousands of price entries listing **ask and bid** prices. You can't read all that to work out how the price moved today.
- So the ticks are **categorized into groups**.

## 3. Timeframe = Tick Categorization
- A timeframe is a **data categorization**: ticks formed within a given period are grouped together. That is where the timeframe idea comes from.
- Common timeframes: **1 minute, 5 minutes, 15 minutes, 30 minutes, 1 hour, 1 day, 1 week**.
- Example, the **1-hour timeframe**: group all ticks that formed in one hour, then take the information we want from the group:
  - The **first tick** (the hour's open).
  - The **last tick** (the hour's close).
  - The tick with the **highest** value.
  - The tick with the **lowest** value.
  - Other information, such as the **average** (middle tick).
- We extract only the points we need and do **not** consider every tick. One hour can have hundreds of ticks, which is hard to consider. The extracted values **represent that hour**.
- **Very long periods** (years of data): we can't consider every tick. It would be a huge number of pages and take too much storage. So the ticks are categorized **monthly or weekly**, and the important values are extracted.
- The grouping doesn't have to be hourly. It can be 5 minutes or 1 minute.
  - For short-term ticks, where the number of ticks is small, you can group per minute.
  - For a wider timeframe, grouping per minute would still produce too much information, so you widen it to per hour, per 4 hours, or per day.

## 4. Chart Types
- A price list is hard to understand as numbers, so it is expressed visually to make understanding and decisions easier.
- **Three major chart types** are used: **Line chart**, **Bar chart (OHLC)**, and **Candlestick chart**.
- Many more types exist (e.g., area charts). They can't all be covered here, and you can explore them yourself. Most work by the same mechanism; the common ones are covered here.

### 4.1 Line Chart
- The **easiest representation of price**. It doesn't give much information, but it is very easy to see.
- It takes **one point per timeframe**.
  - Example: with a 1-day timeframe, it takes one price per day, representing that day, and connects those prices.
- On TradingView and on brokers' charts, the **default is the closing price**. Most platforms let you change this in settings, but closing is the default.
  - Example: with a 1-hour timeframe, take each hour's closing price (4 o'clock close, 5 o'clock close, 6, 7, 8, …) and connect them.
- It gives a simple picture: is the price going up, going down, or consolidating sideways? It shows whether the price is rising strongly or falling.
- **Problem**: not much information. It describes each timeframe with only one point. It only helps in general to know the trend: up, down, or sideways.

### 4.2 Bar Chart (OHLC)
- Gives **four price points (four ticks) per timeframe**: Open, High, Low, Close (the video calls it the OHLC chart).
- A bar has two kinds of parts:
  - One **vertical line**.
  - Two short **horizontal lines (ticks)** on its sides.
- **Left horizontal tick** = the **opening price** of that timeframe (1 minute, 1 hour, 1 day, whatever it is). For a day timeframe, it is the day's opening price.
- **Right horizontal tick** = the **closing price** of that timeframe.
- **Bottom of the vertical line** = the **lowest tick price** in that timeframe (e.g., for a 1-hour timeframe, the lowest price in that hour).
- **Top of the vertical line** = the **highest tick value** in that timeframe.
- **Bull bar**: price rose within the timeframe, so the closing price is **higher** than the opening price. ("Bull" in trading terminology means up/rising.)
- **Bear bar**: price fell, so the closing price is **lower** than the opening price.
- Bull bars are often colored **green** and bear bars **red** to help us see them. Even with the same color, you can still tell bull from bear, because the **left** tick is the opening and the **right** tick is the closing. Look at which is higher.

### 4.3 Candlestick Chart
- The **most famous** one. Candlestick and bar charts give **the same information**.
- A candle has two parts:
  1. **Body**: the rectangle.
  2. **Shadow / wick**: the lines going up and down.
- **Bull candle**: opening price is **lower** than closing price (the price rose).
- **Bear candle**: opening price is **higher** than closing price (the price fell).
- **Colors**: usually a bull candle is **green** (or blue, or an unfilled/hollow body), and a bear candle is **red or black**. You can customize this. By default, TradingView uses green for bull and red for bear.
- **Body**:
  - Bull candle: the **bottom** of the rectangle is the **opening** price and the **top** is the **closing** price.
  - Bear candle: the **top** of the rectangle is the **opening** price and the **bottom** is the **closing** price.
- **Wicks** (same as the bar chart): the upper end is the **highest tick price** and the lower end is the **lowest tick price** in the timeframe.
- **Why candles over bars**: with a bar chart, if the colors aren't told apart, bull and bear are hard to see. In bar charts, the thin lines make it hard to read when many bars sit side by side, and it can be uncomfortable to view. Candles are visually easier.
- Candlestick charts are the common ones on TradingView and in finance in general. They are called **Japanese candlesticks** and originated in Japan.
- **All later charting sessions in this series use candlestick charts.** The presenter hopes beginners now understand what each part means and which tick value each part represents.

## 5. Trader Types and Timeframes

### 5.1 Scalpers
- Take **very fast trades**: within seconds or a few minutes. They open a trade and close it immediately.
- They need to sit at the chart, watch closely, and **decide immediately**. Trades may last only seconds. There is not much thinking, and once they see an opportunity they click fast, and close just as fast.
- Timeframes: **1 minute to 15 minutes**. Because they open and close trades quickly, they need a lot of data, so they focus on the 1-minute to 15-minute charts.

### 5.2 Day Traders
- Don't open trades immediately like scalpers. Trades open and close **within one day** (e.g., open in the morning, close in the afternoon). The trade interval is within 24 hours.
- The presenter thinks many of us will end up day traders, especially those who have time.
- Timeframes: from **15 minutes to about 1 hour**. This is for **execution** (opening trades), though other timeframes can be viewed too; that is covered later in the series. To open and close a trade, they work from timeframes of about 15 minutes upward.

### 5.3 Swing Traders
- Open a trade and close it **within days**. For example, a trade opened Monday may close Wednesday. They hold a trade for two to three days.
- Execution timeframe: **1 hour to 4 hours**.

### 5.4 Position Traders
- Open a trade and hold it for **days to months**. For example, a trade opened in **June** (ሰኔ) may be closed in **September** (መስከረም). They hold a trade long term.
- Execution timeframe: from **one day** upward, up to about **a week or a month**.

### 5.5 The Trade-Off: Opportunity vs Predictability
- **Scalpers** get a very large number of opportunities, because their strategy takes fast trades, so they can open and close many trades per day. But the **noise** is higher, which makes it hard to identify the true market direction. The market's **predictability falls** as you go to lower timeframes; the number of trades grows, but the **quality** (confidently predicting the market) shrinks.
- As you go up toward position trading, opportunities fall and predictability rises. A position trader doesn't trade daily, and may take one trade a month. But because the horizon is long, predicting price is easier.
  - Example: with the Ethiopian birr against the dollar, predicting that the dollar will be higher in a month than now is not hard. In our country's context the dollar is rising and does not fall much, although some drops have been seen lately.
  - Predicting over a long period is not very hard in any market. You can't be 100% confident, but predicting one or two months out is not hard.
- Going up in timeframe: predictability rises, quality rises, opportunities drop sharply, and the time to get returns widens.
- Frequency of trades:
  - Day trading: about **1–3 trades per day**.
  - Swing trading: about **1–3 trades per week**.
  - Position trading: about **1 trade per month**.
- **Metric to understand**:
  - **Lower timeframe**: opportunities rise, predictability and quality fall.
  - **Higher timeframe**: predictability and quality rise, opportunities fall.

