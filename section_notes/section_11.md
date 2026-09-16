# Section 11: Trading Platforms (Final Section of Part 1)
*(Extracted from video & module of the course)*

## 0. Series Structure Note
- The course is divided into **4 Parts**. This section is the **11th section overall** and marks the **end of Part 1**.
## 1. What "Trading Platforms" Means
- Recap: brokers and prop firms are institutions that allow us to trade — with a broker, the broker executes your orders (buy/sell).
- But **to access** that buying/selling — i.e., where you go to place a buy or sell order, view market data, and view market charts — you need a way to **connect with the broker**. This connection tool is what's called a **Trading Platform**. These are the platforms that physically allow us to execute trades.

## 2. Three Common Trading Platforms

### A. MetaTrader (MT4 and MT5)
- **MetaTrader** refers to two platforms made by the company MetaQuotes: **MetaTrader 4 (MT4)** and **MetaTrader 5 (MT5)**.
- **MT4**: the earlier one, launched **around 2005**.
- **MT5**: the more recent one, launched **around 2010**.
- Both are available as **mobile applications**, via **web**, and **desktop applications**.
- MetaTrader is the most famous trading platform. It connects with a broker, and it's where you go to buy/sell, view charts, execute trade, and see market data, and even see news.
- But like every trading platform, it doesn't work standalone; you first need a broker, then connect that broker to MetaTrader, and from there you can trade.

#### MT4 vs MT5 — Key Differences
- ==MT4 has **three account/option types**: MetaTrader 4, MetaTrader 5 (mentioned as options within the ecosystem)... —== 
- MT4 is the **older** one and is used by people who have been trading for longtime. It's **more focused specifically on Forex/currency trading**. Even though it's slowly integrating non-forex instruments, it remains forex-focused.
- MT5 is more holistic and supports currencies, stock, indices, crypto, metals, basically every instrument.
- MT5 has a **better/stronger server**, and better **execution speed** than MT4.
- On **trading order types**, MT4 have only **Limit and Stop** order types — it doesn't support**Stop-Limit** order type. This is because Stop-Limit was designed for volatile instruments like indices, and stocks.
- The **Stop-Limit order type was added starting with MT5**.
- Otherwise, the two platforms are largely similar. Some brokers, in fact, are now phasing out MT4 and only offering **MT5**.
- **Important limitation about MetaTrader (both versions)**: these platforms are used **only for execution purposes** — placing orders and managing your position. They are **not** typically used for chart analysis, even though they have charts.
- **Expert Advisors (EAs) / Trading Bots**: on the **desktop version** (not on mobile) only, MetaTrader (both MT4 and MT5) allows integrating **Expert Advisors** (aka **trading bots**). These let you take an objective/rule-based trading strategy, code it, and have the platform automatically execute trades based on that logic. MetaTrader has its own coding language for building these. Various pre-made trading bots also exist that can be purchased.
### B. TradingView
- It is as a very common platform, mainly used for **charting**.
- TradingView is a large organization with **many data vendors (brokers)**. Example given: viewing a **Gold** chart, you could compare the chart from **IC Markets**, **Pepperstone**, **FXCM**, or from **futures markets / CME** — a lot of multiple data source options are available.
- It's described as the **major platform used for charting**.
- TradingView has its own **community**: you can **publish your own ideas/chart analyses**, other people can **follow** you and view them, you can **comment**, and view other people's analyses. You can see analyst videos, and news from various sources. 
- **TradingView can also be used for execution** (placing orders) — but this depends on whether your broker supports integration with TradingView. Example given: brokers such as **IC Market** and **Pepperstone** support integration with TradingView. 
- As with MetaTrader, TradingView also supports **Expert Advisor-type automation** and **custom indicators**: it has its own coding language called **Pine Script**.
- Summary: most traders use TradingView for **charting** and **community/news provider**, while using **MetaTrader for execution**.

### C. cTrader
- It is newer and **more modern alternative** to MetaTrader. It is not part of MetaTrader — but it serves a similar purpose, mostly used **for execution**.
- Some brokers allow trading through cTrader. E.g. IC Markets allows execution on cTrader along with MetaTrader and Tradingview; **most brokers use MetaTrader**.
- **cTrader specifically tends to be used for ECN-level accounts** (ECN accounts have row/zero spread, execution goes directly to the network).
- Since it is newer, cTrader has a strong/good server, and good execution speed — particularly suited to **scalping**.
- Like the other platforms, cTrader also supports integrating **Expert Advisors / trading bots** and coding custom strategies into it.
- Recommended particularly for people using **ECN accounts** and scalpers, if your broker allows it.

## 3. Broker-Specific Applications (Beyond the Three Above)
- **Brokers are increasingly developing their own applications**.
- Example given: **Exness** — widely used. Exness has its own **Exness App** (a trading app) — meaning even without using MetaTrader, cTrader, or even TradingView, you could use Exness terminal.
- **"Exness Terminal"** is Exness's own **charting site**, which has largely copied most of TradingView's features. Also, within the "Exness Terminal" charting platform itself or using the Exness App, you can execute orders.
- General note: **many more options are emerging over time** as brokers build out their own tools, but the **common industry-standard applications remain**: **MetaTrader** (for execution), **TradingView** (for charting), and **cTrader** (for ECN-level execution).
- Which specific app you end up using also depends on your **broker** — e.g. Exness is common in Ethiopia, if you use Exness, the **Exness App** and **Exness Terminal** are good options.
***
# Supplement to Section 11 — MT5 Mobile App Walkthrough

- On first opening the app with no account added, MT5 defaults to offering its **own demo account** — you dismiss this to connect your actual broker instead.
- To connect a broker: **Settings → Trade Accounts → "+"** → search and select your broker.
- For **Exness** specifically (since common in Ethiopia), you must select the server group **"Exness Technologies Ltd"** among the regional server options shown.
- Each trading account (demo or real) has **its own separate password**, distinct from your main Exness account login password.
- The app is organized into tabs: **Quotes**, **Chart**, **Trade**, and **Settings**, History sits alongside Trade.
- **Quotes tab**: ask price shown top-right (larger), bid bottom-left (smaller), plus daily low/high, spread value, time, and tick increment/% change per instrument. Instruments shown can be added, removed, or reordered via an Edit mode.
- **Chart tab**: switch timeframe (e.g., M15 → M5) and switch instrument from the same screen. Includes a drawing tool (e.g., horizontal lines, deletable) and a one-click order panel (Buy/Sell buttons with adjustable lot size) for instant market execution.
- **Graphical order placement**: you can drag directly on the chart to place pending orders — MT5 automatically infers Stop vs. Limit type based on where you drop the line relative to the current price (e.g., dragging above current price sets a Buy Stop; below sets a Sell Stop). SL/TP can also be set graphically by dragging, with the app showing live estimated profit/loss in dollars before you confirm. A one-time terms acceptance prompt appears on your very first order.
- **Trade tab**: shows open positions and pending orders; lets you place new orders (Market or Pending, with Buy/Sell/Buy Stop/Sell Stop/Buy Limit/Sell Limit/Stop Limit types), and edit SL/TP on existing positions.
- **Partial close** is supported — e.g., closing 0.05 lots of a 0.1-lot position leaves 0.05 still open, recorded as a partial in history.
- Batch controls exist to **close all losing positions** or **close all profitable positions** at once.
- **History tab** has three distinct views: **Orders** (entry and exit shown as separate order records), **Deals** (also separate in/out), and **Positions** (merges a trade's entry and exit into a single combined row).
- **Settings tab**: add further accounts, view the economic calendar, and adjust general app settings.
