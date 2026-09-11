"""
Test challenge bot -- a throwaway stand-in for your real challenge bot,
used ONLY for testing run_challenge.py against a private test channel
without bothering real users.

This is a Telegram BOT (not a user session) -- it runs as
birrforex_challenge_test_bot and responds to whoever messages it, exactly
the way your real bot responds, so run_challenge.py can be pointed at it
and exercise the exact same code paths (URL-button deep link, callback
button clicks, question/answer flow) as it does against production.

Flow it implements (mirrors the real bot):
  0. You message this bot privately with /post_join -- it posts the
     "Join Challenge" message (with a URL button deep-linking to itself)
     into your test channel for you, same convenience your real bot gives
     you for the real channel. Requires the bot to be a channel admin
     with "Post Messages" permission, and TEST_CHANNEL_ID +
     TEST_ADMIN_USER_ID to be set (see config below).
  1. Someone opens that link -> Telegram sends this bot /start <payload>.
  2. Bot replies with a welcome message + a "START QUIZ" callback button.
  3. On that click, sends Question 1/5 with A-D callback-button options,
     pulled from a small built-in bank of random forex questions.
  4. On each answer click (whatever was picked), sends the next question.
  5. After Question 5's answer, sends a "Challenge complete" message.

Runs for a bounded window then exits -- meant to be started right before
you post to the test channel, and left running for the length of your
test session. See the matching GitHub Actions workflow for how it's
started/stopped.
"""

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("test_bot")

BOT_TOKEN = os.environ["TEST_BOT_TOKEN"]
RUN_MINUTES = float(os.environ.get("TEST_BOT_RUN_MINUTES", "10"))

# Mirrors run_challenge.py's real-mode gate: before this clock time (UTC),
# /start is rejected with the exact same rejection text the real challenge
# bot uses, so run_challenge.py's retry-until-active logic gets exercised
# for real in test mode too, not just simulated. Required -- there's no
# sensible default, since it's meant to match what you typed into the
# "Run Challenge Tester" workflow's test_activation_time_utc input.
TEST_ACTIVATION_TIME_UTC = os.environ["TEST_ACTIVATION_TIME_UTC"]  # HH:MM, UTC
CHALLENGE_NOT_ACTIVE_TEXT = "This challenge is not active yet."

# Needed only for the /post_join command (posting the "Join Challenge"
# message to your test channel on your behalf). TEST_CHANNEL_ID is the
# numeric channel ID (e.g. "-1001234567890"). TEST_ADMIN_USER_ID restricts
# who can trigger it -- your own numeric Telegram user ID (get it from
# @userinfobot). If TEST_ADMIN_USER_ID isn't set, /post_join is disabled
# entirely rather than left open to anyone who messages the bot.
TEST_CHANNEL_ID = os.environ.get("TEST_CHANNEL_ID")
TEST_ADMIN_USER_ID = os.environ.get("TEST_ADMIN_USER_ID")
TEST_BOT_USERNAME = os.environ.get("TEST_BOT_USERNAME", "birrforex_challenge_test_bot")


def activation_time_today_utc() -> datetime:
    """Parses TEST_ACTIVATION_TIME_UTC ('HH:MM') into today's UTC datetime."""
    hour, minute = (int(p) for p in TEST_ACTIVATION_TIME_UTC.split(":"))
    now = datetime.now(timezone.utc)
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

# ----------------------------------------------------------------------
# Real questions captured from the LIVE challenge bot (screenshots,
# 2026-09-04), cross-checked against the user's official answer key
# (also 2026-09-04). Fixed into 3 quiz sessions -- one per real quiz you
# screenshotted -- instead of one shuffled pool, so each session's 5
# questions always appear together as a set (order within a quiz doesn't
# matter, confirmed with user). Pick which quiz to run via
# TEST_QUIZ_SELECTION (env var / workflow dropdown: "quiz1"/"quiz2"/"quiz3",
# or "random" to pick one of the 3 at random each session).
#
# IMPORTANT: your real bot mixes two button styles across questions --
# some show the full option text on the button (e.g. "A) Going Short"),
# others show ONLY the bare letter ("A"/"B"/"C"/"D") with the actual
# options listed inside the question text instead. run_challenge.py
# reads options straight off the BUTTON text (see extract_mcq_options),
# so a bare-letter question is a materially harder test for Gemini --
# it has to read the options out of the question body, not the button.
# This bank includes both styles on purpose, to match that real
# difficulty rather than test an easier version of the flow.
# ----------------------------------------------------------------------

QUIZ_1_FOREX_BROKERS = [
    # -- Section 4: Understanding Forex Brokers and Their Types --
    {
        "text": (
            "Which of the following is NOT true about Dealing Desk (DD) brokers?\n"
            "A) There is no potential conflict of interest between the broker and the client\n"
            "B) A major source of revenue can be the spread\n"
            "C) They can act as the liquidity provider for their clients\n"
            "D) They can offer relatively tight and fixed spreads"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 0,
        "bare_letters": True,
    },
    {
        "text": "Which type of broker routes client orders to preselected liquidity providers?",
        "options": ["STP", "ECN", "Dealing Desk", "Hybrid"],
        "correct": 0,
        "bare_letters": False,
    },
    {
        "text": (
            "Which of the following is TRUE about Forex brokers?\n"
            "A) ECN brokers route client orders directly to the global Forex market\n"
            "B) STP brokers have no spread and only earn through commissions\n"
            "C) STP brokers usually add a markup to the spread received from liquidity providers\n"
            "D) Dealing Desk brokers do not provide their own quotes"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 2,
        "bare_letters": True,
    },
    {
        "text": (
            "Which of the following is FALSE about the Forex market and broker execution?\n"
            "A) There is no single centralized global Forex exchange\n"
            "B) One broker can use another broker or dealer as a liquidity provider\n"
            "C) An ECN broker routes orders through its ECN/liquidity network rather than to one global Forex exchange\n"
            "D) A Non-Dealing Desk broker can act as the liquidity provider for the same client order"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 3,
        "bare_letters": True,
    },
    {
        "text": "What is a Liquidity Provider?",
        "options": [
            "A firm that provides prices and facilitates trade execution",
            "A company that manages traders' accounts",
            "A firm that connects traders with brokers",
            "A company that regulates financial markets",
        ],
        "correct": 0,
        "bare_letters": False,
    },
]

# -- Section 5: Understanding Currency Pairs (Trading Instruments) --
# Includes the 2 video-referencing questions ("in the Section Video...") --
# the AI has no access to that video, so these are UNANSWERABLE from the
# question text alone. Correct answers below are the user's official
# answer key -- used for scoring only, NOT given to the AI, which still
# has to guess blind. Kept in this quiz's fixed set (not toggled
# separately) since they're genuinely part of this real quiz session.
QUIZ_2_CURRENCY_PAIRS = [
    {
        "text": "When we execute a Buy order which price are we using",
        "options": ["Spread", "Average of ASK and BID", "BID", "ASK"],
        "correct": 3,
        "bare_letters": False,
    },
    {
        "text": "what is the smallest price level a currency pair can increase or decrease in modern market",
        "options": ["price fraction", "Pip", "Tick", "Point"],
        "correct": 3,  # "Point" -- corrected against official answer key (was wrongly "Pip")
        "bare_letters": False,
    },
    {
        "text": (
            "which one we consider as pip in XAUUSD (GOLD) Pair Commonly\n"
            "A) The 4th decimal place\n"
            "B) The 1st decimal place\n"
            "C) The 2nd decimal place\n"
            "D) The 1st digit before the decimal point"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,  # "The 1st decimal place" -- corrected against official answer key (was wrongly A)
        "bare_letters": True,
    },
    {
        "text": "in the USDJPY Example given in the Section Video What is the Spread",
        "options": ["11 pip", "11 point", "12 point", "12 pip"],
        "correct": 1,  # "11 point" -- corrected against official answer key (was wrongly "11 pip")
        "bare_letters": False,
    },
    {
        "text": "In the section Video what pair is given as second example on Minor Pairs",
        "options": ["USD CAD", "EUR JPY", "EUR GBP", "GBP JPY"],
        "correct": 3,  # "GBP JPY" -- corrected against official answer key (was wrongly "EUR JPY")
        "bare_letters": False,
    },
]

# -- Section 2: CFD (Contract for Difference) --
QUIZ_3_CFD = [
    {
        "text": (
            "Which of the following is a leading global futures marketplace, known for "
            "benchmark futures contracts across major asset classes?\n"
            "A) Intercontinental Exchange (ICE)\n"
            "B) Eurex\n"
            "C) Chicago Mercantile Exchange (CME)\n"
            "D) New York Stock Exchange (NYSE)"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 2,
        "bare_letters": True,
    },
    {
        "text": (
            "Which of the following is NOT true about CFDs?\n"
            "A) The trader does not own the underlying asset\n"
            "B) CFDs are traded on centralized exchanges\n"
            "C) You go long if you expect the asset price to rise\n"
            "D) CFDs can provide flexible and accessible exposure to different markets"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "bare_letters": True,
    },
    {
        "text": (
            "Which of the following is NOT true about Futures and CFDs?\n"
            "A) CFDs have a centralized order book\n"
            "B) Futures can be traded without owning the underlying asset, to profit from price movements\n"
            "C) Futures have a centralized order book\n"
            "D) CFDs are traded OTC"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 0,
        "bare_letters": True,
    },
    {
        "text": (
            "Which of the following is TRUE about futures trading?\n"
            "A) Futures are traded OTC\n"
            "B) Futures trading does not involve leverage\n"
            "C) You can trade futures based on price movements without owning the asset\n"
            "D) Futures trading has no margin requirement"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 2,
        "bare_letters": True,
    },
    {
        "text": "What do we call selling an asset you do not currently own, with the aim of benefiting if its price falls?",
        "options": ["Going Short", "Hedging", "Selling on Margin", "Going Long"],
        "correct": 0,
        "bare_letters": False,
    },
]

# -- Section 6: Position Sizing, Order Types and Placing Your Trades --
# NEW 2026-09-05: 15 questions written by Claude from the user's uploaded
# Section 6 PDF module (position sizing/lots, order flow, order types,
# risk management orders), split into 3 new fixed 5-question quizzes
# (quiz4/5/6) matching the module's own internal structure -- not derived
# from the earlier section_6.md video-transcript notes, though a couple
# of non-conflicting details from those notes are folded in where the PDF
# itself doesn't give a number (flagged inline below). No video-reference
# ("in the Section Video...") questions here -- the PDF is a static
# document, not a video, so nothing is unanswerable-from-text the way
# QUIZ_2's video questions are.

# -- Position Sizing: Lots --
QUIZ_4_POSITION_SIZING = [
    {
        "text": "How many units of the base currency does 1 Standard Lot represent?",
        "options": ["1,000", "10,000", "100,000", "1,000,000"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": (
            "Which of the following correctly matches a lot size to its unit value?\n"
            "A) Mini Lot = 100,000 units\n"
            "B) Micro Lot = 10,000 units\n"
            "C) Standard Lot = 1,000 units\n"
            "D) Micro Lot = 1,000 units"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 3,
        "bare_letters": True,
    },
    {
        "text": "In a Cent account, what changes compared to a standard account, according to the module?",
        "options": [
            "The lot and unit structure changes completely",
            "The balance is shown in cents, so real-money exposure is much smaller for the same lot/unit structure",
            "Only micro lots are allowed",
            "Leverage is automatically increased",
        ],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": (
            "According to the module's Gold, Oil, and Bitcoin examples, which of the following is TRUE?\n"
            "A) 1 standard lot of Oil is defined as 1,000 barrels\n"
            "B) 1 standard lot of Gold (XAU/USD) is 1,000 troy ounces\n"
            "C) 1 standard lot of Bitcoin is always exactly 1 Bitcoin at every broker\n"
            "D) Lot sizes for non-currency instruments never vary between brokers"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 0,
        "bare_letters": True,
    },
    {
        "text": "In the EUR/USD pip value example, a 1-pip move (1.0000 to 1.0001) on 1 Mini Lot is worth approximately how much profit/loss?",
        "options": ["$10", "$1", "$0.10", "$100"],
        "correct": 1,
        "bare_letters": False,
    },
]

# -- Order Flow and Order Types --
QUIZ_5_ORDER_TYPES = [
    {
        "text": "When you place a Buy or Sell order, what does the module say the broker does with it?",
        "options": [
            "Executes it internally with no counterparty",
            "Routes it into the broader market network to match it with a counterparty willing to take the opposite side",
            "Holds it until the end of the trading day",
            "Sends it directly to a central government exchange",
        ],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": (
            "Which statement about a Market Order is TRUE according to the module?\n"
            "A) It guarantees the exact requested price but not execution\n"
            "B) It guarantees execution at the current best available price, but not the exact price during fast-moving markets\n"
            "C) It only executes once a specific future price level is reached\n"
            "D) It is a type of pending order"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "bare_letters": True,
    },
    {
        "text": "The difference between a requested price and the actual executed price during a fast-moving market is called:",
        "options": ["Spread", "Slippage", "Margin call", "Rollover"],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": (
            "Which pending order places a Buy order ABOVE the current market price, expecting price to break up through that level and keep rising?\n"
            "A) Buy Limit\n"
            "B) Sell Limit\n"
            "C) Buy Stop\n"
            "D) Sell Stop"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 2,
        "bare_letters": True,
    },
    {
        "text": "Which order type combines a Stop and a Limit, triggering a Limit order only once the Stop price is hit?",
        "options": ["Trailing Stop Loss", "Market Order", "Stop-Limit Order", "Take Profit"],
        "correct": 2,
        "bare_letters": False,
    },
]

# -- Order Diagrams and Risk Management Orders --
QUIZ_6_RISK_ORDERS = [
    {
        "text": (
            "A Sell Limit order is placed expecting which price behavior, according to the module's diagram?\n"
            "A) Price rises to your level then falls\n"
            "B) Price falls to your level then rises\n"
            "C) Price breaks up and keeps rising\n"
            "D) Price breaks down and keeps falling"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 0,
        "bare_letters": True,
    },
    {
        "text": "A Sell Stop order is placed BELOW the current market price, expecting:",
        "options": [
            "Price to break down through that level and continue falling",
            "Price to fall to that level then bounce back up",
            "Price to rise to that level then reverse down",
            "Price to stay flat at that level",
        ],
        "correct": 0,
        "bare_letters": False,
    },
    {
        "text": (
            "Which order automatically closes an open position if the price moves AGAINST it to a predefined level, to limit potential loss?\n"
            "A) Take Profit\n"
            "B) Stop Loss\n"
            "C) Trailing Stop Loss\n"
            "D) Buy Limit"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "bare_letters": True,
    },
    {
        "text": "What does a Take Profit (TP) order do, according to the module?",
        "options": [
            "Automatically closes a position at a loss once price moves against you",
            "Automatically closes a position when price reaches a predefined profit level, locking in gains",
            "Increases your position size automatically as profit grows",
            "Cancels all other pending orders on the account",
        ],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": (
            "Which of the following best describes a Trailing Stop Loss?\n"
            "A) A fixed Stop Loss that never moves once set\n"
            "B) A dynamic Stop Loss that automatically moves in your favor as price moves, only ever moving in the profit direction\n"
            "C) An order that only works on pending orders, not open positions\n"
            "D) A Stop Loss that moves against you to reduce broker risk"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "bare_letters": True,
    },
]

# -- Real challenge_35 questions (2026-09-04), verbatim from user screenshots
# -- used throughout this session's Q1-race-condition / click-mode
# investigation. Correct answers confirmed by the user against the bot's
# own "CORRECT ANSWERS" summary message, not guessed -- see context.json.
# Q1 and Q4 use the real bot's bare-letter button style (confirmed via
# inspect_bot_buttons.py, 2026-09-04); Q2/Q3/Q5 use its full-option-text
# style -- matches the real challenge exactly, not a simplified version.
# NAMING (per user, 2026-09-06): "quizN" is reserved for synthetic quizzes
# NOT sourced from a live challenge; anything taken from a real live bot
# run gets a descriptive name instead -- this one is SECTION_6_QUIZ /
# "section_6_quiz", not "quiz7".
SECTION_6_QUIZ = [
    {
        "text": (
            "In which account type can you trade less than 1,000 units of a currency?\n"
            "A) Pro Account\n"
            "B) You cannot trade less than 1,000 units\n"
            "C) Standard Account\n"
            "D) Micro Account"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 3,
        "bare_letters": True,
    },
    {
        "text": "Which order type was specified in the section video as the newer order type usefull in placing order for Volatile Asset?",
        "options": ["Stop", "Limit", "Trailing", "Stop limit"],
        "correct": 3,
        "bare_letters": False,
    },
    {
        "text": "Which instruments were given as examples of \u201codd pairs\u201d where the standard currency contract-sizing rule does not apply?",
        "options": ["Gold, BTC, US500", "XAG, Oil, US30", "XAU, Oil, BTC", "USTEC, Gold, Oil"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": (
            "What is the common contract size for BTCUSD?\n"
            "A) 1 BTC\n"
            "B) 10 BTC\n"
            "C) 1,000 BTC\n"
            "D) 100,000 BTC"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 0,
        "bare_letters": True,
    },
    {
        "text": "Which lot size represents 10,000 units in Forex?",
        "options": ["1 Lot", "0.1 Lot", "Micro Lot", "Standard Lot"],
        "correct": 1,
        "bare_letters": False,
    },
]

# -- Claude-generated quizzes for Section 7 (Leverage & Margin), from
# section_7.md. NAMING CONVENTION (per user, 2026-09-07): "cla_<N><letter>"
# is for Claude-generated quiz questions (as opposed to real questions
# taken from a live challenge bot run, which get a descriptive name like
# SECTION_6_QUIZ above) -- "cla" = Claude, "<N>" = section number, and
# "A"/"B"/"C"... distinguishes multiple Claude-generated sets for the same
# section. CLA_7A = 5 hard conceptual questions (mechanism/definitions, no
# numbers to plug in); CLA_7B = calculation + video-referential questions
# (worked-example numbers, named brokers/thresholds, "what did the video
# state" style) -- both per user's explicit request for "hard conceptual
# and some referential (to the video)" questions.
CLA_7A = [
    {
        "text": (
            "In the house-purchase analogy, why is the bank's 900,000 Birr described as something that "
            "can never actually be lost from the bank's own perspective?\n"
            "A) The bank insures the property separately\n"
            "B) The bank sells the property itself to recover its share once its value falls to that amount, so only the buyer's own contribution absorbs any loss\n"
            "C) The government guarantees the bank's contribution\n"
            "D) The bank and buyer always split any loss equally"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "bare_letters": True,
    },
    {
        "text": "In the Forex translation of the house analogy, what does the trader's own contribution toward a position correspond to?",
        "options": ["Leverage", "Margin", "Equity", "Stop Out"],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": (
            "Why is a profit showing on a currently open position called \"Unrealized\" rather than simply counted as final profit?\n"
            "A) Because taxes haven't been paid on it yet\n"
            "B) Because the broker hasn't approved it yet\n"
            "C) Because the price could still reverse into a loss at any moment before the position is closed\n"
            "D) Because it only becomes real after 24 hours"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 2,
        "bare_letters": True,
    },
    {
        "text": "According to the module, what does a rising Margin Level % indicate about an account, and what does a falling Margin Level % indicate?",
        "options": [
            "Rising = safer/healthier account; falling = increasing losses relative to account size, more likely the broker closes positions",
            "Rising = more risk of a Margin Call; falling = the account is safer",
            "Margin Level % only reflects deposit history, not risk",
            "Rising and falling both indicate the same risk level -- it's a neutral metric",
        ],
        "correct": 0,
        "bare_letters": False,
    },
    {
        "text": (
            "What is the key structural difference between a Margin Call and a Stop Out, according to the module?\n"
            "A) A Margin Call is a warning notification asking the trader to act; a Stop Out is the broker automatically closing positions itself without waiting for the trader\n"
            "B) A Margin Call closes positions automatically; a Stop Out is just a warning\n"
            "C) They are two names for the exact same event\n"
            "D) A Margin Call only applies to Professional accounts; a Stop Out only applies to standard accounts"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 0,
        "bare_letters": True,
    },
]

CLA_7B = [
    {
        "text": (
            "In the worked Margin Level example, Account Balance = $1,000, Leverage = 1:100, and the trader opens 0.1 lot (10,000 units) on EUR/USD. "
            "What is the resulting Margin Level %?\n"
            "A) 100%\n"
            "B) 1,000%\n"
            "C) 10%\n"
            "D) 500%"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "bare_letters": True,
    },
    {
        "text": "In the module's Equity worked example with two open trades (one at +$5, one at -$10) and a $100 Balance, what is the resulting Equity?",
        "options": ["$105", "$95", "$100", "$90"],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": (
            "Which brokers did the video name specifically as issuing a Margin Call at 100% Margin Level (standard accounts)?\n"
            "A) Exness and Pepperstone\n"
            "B) IC Markets and Pepperstone\n"
            "C) Exness and IC Markets\n"
            "D) FXTM and Exness"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "bare_letters": True,
    },
    {
        "text": "Which two brokers did the video name as waiting until 0% Margin Level before triggering Stop Out, because of a feature called Negative Balance Protection?",
        "options": ["Exness and IC Markets", "IC Markets and Pepperstone", "Exness and Pepperstone", "FXTM and IC Markets"],
        "correct": 0,
        "bare_letters": False,
    },
    {
        "text": (
            "In the house-purchase profit scenario, the property was bought at 1,000,000 Birr and sold 6 months later at 1,500,000 Birr. "
            "What profit figure did the video state?\n"
            "A) 900,000 Birr\n"
            "B) 100,000 Birr\n"
            "C) 500,000 Birr\n"
            "D) 1,500,000 Birr"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 2,
        "bare_letters": True,
    },
]

# -- Claude-generated quizzes for Section 8 (Trading Time Sessions), from
# section_8.md. Per the cla_<N><letter> convention (see CLA_7A/CLA_7B
# comment above). These 20 questions (CLA_8A-CLA_8D) were deliberately
# designed per user's explicit request (2026-09-07) to be hard/ambiguous:
# each question's correct answer sits alongside distractor options that are
# real values/facts pulled from elsewhere in section_8.md (an adjacent
# session's time, a sub-market's time instead of the overall session's, a
# kill-zone time instead of a full-session time, a DST-shifted time instead
# of the normal one, etc.) rather than made-up wrong answers -- testing
# whether the model actually distinguishes which specific time range /
# session / fact a question is asking about, rather than pattern-matching
# to the first plausible number. CLA_8A/8B = standard-time-phrased
# questions (5 general session/kill-zone/overlap questions, then 5 on
# characteristics/currencies/spreads/kill-zone-definition/DST-scope).
# CLA_8C = 5 more standard-time-phrased ambiguous questions. CLA_8D = 5
# questions specifically routed through Ethiopian colloquial local time
# (day/night o'clock split, occasionally using "local") per user's
# explicit request to add local-time-based ambiguity too. Per user
# feedback during authoring: options are bare values only (no parenthetical
# hints revealing which section/concept they're borrowed from), and
# question stems avoid over-specifying (e.g. no "(non-DST)" qualifier in
# the stem itself) so the ambiguity is genuine rather than trivially
# resolved by the question wording alone.
CLA_8A = [
    {
        "text": "When does the New York trading session close?",
        "options": ["12:00 PM", "8:00 PM", "1:00 AM", "6:00 PM"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": "When does the Asian trading session open?",
        "options": ["12:00 AM", "3:00 AM", "9:00 AM", "4:00 PM"],
        "correct": 0,
        "bare_letters": False,
    },
    {
        "text": "When does the Asian trading session close?",
        "options": ["9:00 AM", "12:00 PM", "11:00 AM", "12:00 AM"],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": "What is the New York Kill Zone time range?",
        "options": ["4:00 PM to 8:00 PM", "4:00 PM to 6:00 PM", "4:00 PM to 1:00 AM", "9:00 PM to 11:00 PM"],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": "What is the London Kill Zone time range?",
        "options": ["11:00 AM to 1:00 PM", "11:00 AM to 8:00 PM", "10:00 AM to 12:00 PM", "4:00 PM to 6:00 PM"],
        "correct": 0,
        "bare_letters": False,
    },
]

CLA_8B = [
    {
        "text": "What is the time range of the London-New York overlap?",
        "options": ["4:00 PM to 6:00 PM", "11:00 AM to 8:00 PM", "4:00 PM to 8:00 PM", "4:00 PM to 1:00 AM"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": "When does the New York session open?",
        "options": ["4:00 PM", "3:00 PM", "5:00 PM", "12:00 PM"],
        "correct": 0,
        "bare_letters": False,
    },
    {
        "text": "When does the London session close?",
        "options": ["8:00 PM", "7:00 PM", "6:00 PM", "1:00 AM"],
        "correct": 0,
        "bare_letters": False,
    },
    {
        "text": "What are the characteristics of the London session?",
        "options": [
            "Low movement, sideways, wider spreads",
            "Strong, impulsive movement, tighter spreads",
            "Can strongly continue or fully reverse the prior session's direction",
            "Most active session due to two regions overlapping",
        ],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": "What are the characteristics of the New York session?",
        "options": [
            "Strong, impulsive movement, tighter spreads",
            "Can strongly continue the prior impulse or reverse it entirely",
            "Low movement, sideways, wider spreads",
            "Historically low volume, but improving",
        ],
        "correct": 1,
        "bare_letters": False,
    },
]

CLA_8C = [
    {
        "text": "Which currencies are most active during the Asian session?",
        "options": ["EUR, GBP, CHF", "USD, CAD", "AUD, NZD, JPY", "AUD, NZD, JPY, USD"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": "Are spreads typically wider or tighter during the Asian session?",
        "options": [
            "Wider",
            "Tighter, since volume has been improving",
            "Tighter, same as London and New York",
            "Wider, because of Daylight Saving Time",
        ],
        "correct": 0,
        "bare_letters": False,
    },
    {
        "text": "How long does a Kill Zone typically last?",
        "options": ["1 hour", "1-2 hours", "2 hours", "It varies, with no defined length"],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": "Does the Asian session observe Daylight Saving Time?",
        "options": ["No", "Yes, shifting by about an hour", "Mostly no, except Sydney", "Only Tokyo does"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": "What is the overall time range of the Asian trading session?",
        "options": ["12:00 AM to 9:00 AM", "3:00 AM to 12:00 PM", "12:00 AM to 12:00 PM", "12:00 AM to 8:00 PM"],
        "correct": 2,
        "bare_letters": False,
    },
]

CLA_8D = [
    {
        "text": "What time does the London session open?",
        "options": ["5 o'clock", "11 o'clock", "4 o'clock", "10 o'clock"],
        "correct": 0,
        "bare_letters": False,
    },
    {
        "text": "During Daylight Saving Time, what time does the New York session close?",
        "options": ["nighttime 7 o'clock", "nighttime 6 o'clock", "nighttime 1 o'clock", "12:00 o'clock"],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": "What local time does the Tokyo market close?",
        "options": ["morning 3 o'clock", "midday 6 o'clock", "nighttime 9 o'clock", "nighttime 6 o'clock"],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": "What local time does the Sydney market open?",
        "options": ["nighttime 6 o'clock", "morning 3 o'clock", "3 AM", "nighttime 9 o'clock"],
        "correct": 0,
        "bare_letters": False,
    },
    {
        "text": "What local time does the New York Kill Zone end?",
        "options": ["12:00 o'clock", "10 o'clock", "nighttime 2 o'clock", "7 o'clock"],
        "correct": 0,
        "bare_letters": False,
    },
]

# -- Real challenge-bot questions for Section 8 (Trading Time Sessions),
# captured live from an actual challenge run (2026-09-09) and confirmed
# against the bot's own "CORRECT ANSWERS" summary message, not guessed --
# see context.json. Q1 (NY session start time) and Q5 (2nd active Asian
# currency) use the real bot's bare-letter button style (A/B/C/D with the
# real option text embedded in the question message itself); Q2 (2nd
# European market), Q3 (most active session), Q4 (kill-zone name) use its
# full-option-text button style -- matches the real challenge exactly, not
# a simplified version. NAMING (per user, 2026-09-06 convention): "quizN"
# is reserved for synthetic quizzes NOT sourced from a live challenge;
# anything taken from a real live bot run gets a descriptive name instead
# -- this one is SECTION_8_QUIZ / "section_8_quiz", not "quiz7".
SECTION_8_QUIZ = [
    {
        "text": (
            "In DLS times when will be the appx starting time of NY session?\n"
            "A) 12:00 PM\n"
            "B) 3:00 PM\n"
            "C) 8:00 AM\n"
            "D) 4:00 PM"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "bare_letters": True,
    },
    {
        "text": "in the section video, what is the 2nd given market from European (London) session",
        "options": ["Frankfurt", "London", "Zurich", "Paris"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": "Which session is the most active one",
        "options": ["Asian - London", "London - NY", "Asian", "London"],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": "What do we call the first 1-2 hours of a session",
        "options": ["Pump Zone", "Kill zone", "Opening Session", "Active Zone"],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": (
            "In the section video, from the given example on the active currencies in Asian time which currency is mentioned 2nd?\n"
            "A) AUD\n"
            "B) CNY\n"
            "C) NZD\n"
            "D) JPY"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 2,
        "bare_letters": True,
    },
]

# -- Real challenge-bot questions for Section 9 (Account Types), captured
# live from an actual challenge run (2026-09-11) and confirmed against the
# bot's own "CORRECT ANSWERS" summary message (screenshot), not guessed.
# All 5 questions use the real bot's full-option-text button style (no
# bare-letter questions in this run, unlike section_6_quiz/section_8_quiz).
# NAMING (per the 2026-09-06 convention): "quizN" is reserved for synthetic
# quizzes NOT sourced from a live challenge; this is SECTION_9_QUIZ /
# "section_9_quiz", not "quiz7".
SECTION_9_QUIZ = [
    {
        "text": "Which is not a Market Difference between Demo and Real account?",
        "options": ["Slippage", "Spread Variablity", "Greed", "Execution Speed"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": "Which acct is better for Beginners to get real acct experiance",
        "options": ["Standard", "Pro", "Cent", "ECN"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": "which is True about Cent/Micro Accounts",
        "options": ["High Deposit requirment", "Spread is usually lower", "More Slippage", "Low Spread Variablity"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": "what is the smallest contact size we can hold in currency pairs considering all types of trading accounts",
        "options": ["1000 Unit", "100000 Unit", "10000 Uinit", "10 Unit"],
        "correct": 3,
        "bare_letters": False,
    },
    {
        "text": "Which Accts are considered Better For Scalping from the others",
        "options": ["Standard", "Pro", "Cent", "ECN"],
        "correct": 3,
        "bare_letters": False,
    },
]

# Each quiz stays together as its own fixed 5-question set, matching how
# they were really presented -- not shuffled or mixed with the others.
QUIZ_SESSIONS = {
    "quiz1": QUIZ_1_FOREX_BROKERS,
    "quiz2": QUIZ_2_CURRENCY_PAIRS,
    "quiz3": QUIZ_3_CFD,
    "quiz4": QUIZ_4_POSITION_SIZING,
    "quiz5": QUIZ_5_ORDER_TYPES,
    "quiz6": QUIZ_6_RISK_ORDERS,
    "section_6_quiz": SECTION_6_QUIZ,
    "cla_7a": CLA_7A,
    "cla_7b": CLA_7B,
    "cla_8a": CLA_8A,
    "cla_8b": CLA_8B,
    "cla_8c": CLA_8C,
    "cla_8d": CLA_8D,
    "section_8_quiz": SECTION_8_QUIZ,
    "section_9_quiz": SECTION_9_QUIZ,
}

TOTAL_QUESTIONS = 5
LETTERS = ["A", "B", "C", "D", "E", "F"]

# Which quiz session to run: any key in QUIZ_SESSIONS (currently
# quiz1-quiz6 plus section_6_quiz -- see NAMING note above SECTION_6_QUIZ),
# or "random" to pick one at random each session. Replaces the old
# TEST_INCLUDE_VIDEO_QUESTIONS boolean -- selecting quiz2 naturally
# includes its 2 video-reference questions since they're part of that
# real quiz, no separate toggle needed. Defaults to "random" so ordinary
# test runs still vary session to session like before.
TEST_QUIZ_SELECTION = os.environ.get("TEST_QUIZ_SELECTION", "random").lower()

# Per-chat quiz state: chat_id -> {
#   "questions": [...], "index": int,
#   "started_at": float | None,  # time.monotonic() when Q1 was sent (quiz clock starts here)
#   "correct_count": int,        # right answers so far
# }
_sessions: dict[int, dict] = {}


def build_question_message(q_index: int, questions: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    q = questions[q_index]
    text = f"Question {q_index + 1}/{TOTAL_QUESTIONS} ⏱️ {q['text']}"

    if q.get("bare_letters"):
        # Options are embedded in the question text; buttons are bare
        # letters only -- matches your real bot's Q2-Q4 style.
        buttons = [
            [InlineKeyboardButton(LETTERS[i], callback_data=f"answer:{q_index}:{i}")]
            for i in range(len(q["options"]))
        ]
    else:
        # Full option text on the button -- matches your real bot's Q1/Q5 style.
        buttons = [
            [InlineKeyboardButton(f"{LETTERS[i]}) {opt}", callback_data=f"answer:{q_index}:{i}")]
            for i, opt in enumerate(q["options"])
        ]
    return text, InlineKeyboardMarkup(buttons)


async def post_join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Message this bot privately with /post_join and it posts the
    "Join Challenge" message (with the deep-link URL button) into your
    test channel for you -- same convenience your real bot gives you,
    just for the test channel.

    Restricted to TEST_ADMIN_USER_ID. If that's not configured, this
    command is disabled entirely (fail-closed, not open to anyone who
    finds the bot).
    """
    sender_id = update.effective_user.id if update.effective_user else None

    if not TEST_ADMIN_USER_ID:
        await update.message.reply_text(
            "/post_join is disabled: TEST_ADMIN_USER_ID isn't configured. "
            "Set it to your numeric Telegram user ID (from @userinfobot) to enable this."
        )
        return

    if str(sender_id) != str(TEST_ADMIN_USER_ID):
        log.warning(f"/post_join attempted by unauthorized user {sender_id}")
        await update.message.reply_text("Not authorized to use this command.")
        return

    if not TEST_CHANNEL_ID:
        await update.message.reply_text(
            "TEST_CHANNEL_ID isn't configured -- can't post. Set it to your test channel's numeric ID or @username."
        )
        return

    # The Bot API's chat_id needs either a numeric ID, or a username WITH
    # the leading "@" -- a bare username string ("test_channelmania") is
    # not a recognized chat identifier and comes back as "chat not found",
    # even though Telethon (used elsewhere in this project, via a user
    # session) is more lenient and accepts it without the "@". Normalize
    # here so either form works in the secret.
    raw_channel = TEST_CHANNEL_ID.strip()
    if raw_channel.lstrip("-").isdigit():
        target_chat_id = int(raw_channel)  # numeric ID, e.g. -1001234567890
    elif raw_channel.startswith("@"):
        target_chat_id = raw_channel
    else:
        target_chat_id = f"@{raw_channel}"  # bare username -> add the required "@"

    # A fresh payload each time, so old deep links from a previous test
    # don't get confused with the current one in your run_challenge.py logs.
    payload = f"test_challenge_{int(random.random() * 1_000_000)}"
    deep_link = f"https://t.me/{TEST_BOT_USERNAME}?start={payload}"

    post_text = "📢 Join Challenge Now! (TEST)"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Join Challenge Now", url=deep_link)]])

    try:
        await context.bot.send_message(chat_id=target_chat_id, text=post_text, reply_markup=keyboard)
    except Exception as e:
        log.error(f"Failed to post to test channel: {e}")
        await update.message.reply_text(
            f"Couldn't post to the channel: {e}\n\n"
            "Common cause: this bot isn't an admin of the test channel yet, "
            "or doesn't have 'Post Messages' permission."
        )
        return

    await update.message.reply_text(f"Posted to the test channel.\nDeep link used: {deep_link}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles /start <payload> -- this is what a real user's tap on the
    channel's URL button triggers (and what click_button_or_follow_deep_link
    replicates for a URL button in run_challenge.py).

    Rejects with CHALLENGE_NOT_ACTIVE_TEXT before TEST_ACTIVATION_TIME_UTC,
    mirroring the real challenge bot's own activation gate -- this is what
    lets run_challenge.py's retry-until-active logic be exercised for real
    in test mode, not just assumed to work.
    """
    chat_id = update.effective_chat.id
    payload = context.args[0] if context.args else "(none)"

    now = datetime.now(timezone.utc)
    activation_time = activation_time_today_utc()
    if now < activation_time:
        log.info(
            f"/start received from chat {chat_id} at {now.strftime('%H:%M:%S')} UTC, "
            f"payload={payload} -- rejecting, not active until {TEST_ACTIVATION_TIME_UTC} UTC"
        )
        await update.message.reply_text(f"❌ {CHALLENGE_NOT_ACTIVE_TEXT}")
        return

    log.info(f"/start received from chat {chat_id}, payload={payload}")

    if TEST_QUIZ_SELECTION == "random":
        quiz_key = random.choice(list(QUIZ_SESSIONS.keys()))
    elif TEST_QUIZ_SELECTION in QUIZ_SESSIONS:
        quiz_key = TEST_QUIZ_SELECTION
    else:
        log.warning(
            f"TEST_QUIZ_SELECTION={TEST_QUIZ_SELECTION!r} not recognized "
            f"(expected one of {list(QUIZ_SESSIONS.keys())} or 'random') -- "
            "falling back to a random quiz"
        )
        quiz_key = random.choice(list(QUIZ_SESSIONS.keys()))

    # Fixed set, in the order defined above -- order within a quiz doesn't
    # matter (confirmed with user), so no shuffling of the 5 questions.
    questions = list(QUIZ_SESSIONS[quiz_key])
    log.info(f"Quiz session selected: {quiz_key}")

    _sessions[chat_id] = {
        "questions": questions,
        "index": 0,
        "started_at": None,  # set right below, when Q1 actually goes out
        "correct_count": 0,
    }

    welcome_text = (
        "📚 Welcome to My Personal Challenge Guys! 📊\n"
        "Section: Forex Basics (TEST)\n\n"
        "Tap below when you're ready to begin."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("START QUIZ", callback_data="start_quiz")]])
    await update.message.reply_text(welcome_text, reply_markup=keyboard)

    # IMPORTANT (derived from live run_challenge.py debug logs on two
    # separate accounts, 2026-09-04 -- see run_challenge.py's own comments
    # for the timestamps): the Welcome message and Question 1 arrived
    # 0-1 seconds apart in both runs, and BOTH arrived well before the
    # script's Start Quiz click completed (~15s later in both runs). That
    # ordering means the real bot does not wait for the "START QUIZ"
    # button to be tapped before sending Q1 -- it sends both essentially
    # together. This used to be modeled the opposite way here (Q1 only
    # sent from button_handler on "start_quiz"), which meant this test bot
    # could never exercise the real race condition that broke
    # run_challenge.py in production: a run against this bot always had a
    # Q1 listener registered in plenty of time, because Q1 literally
    # couldn't exist yet at that point. Sending Q1 here, unconditionally,
    # right after the welcome message, is what makes this test bot an
    # actual regression test for that bug rather than a flow that happens
    # to avoid it.
    session = _sessions[chat_id]
    session["started_at"] = time.monotonic()  # quiz clock starts as Q1 goes out
    text, markup = build_question_message(0, session["questions"])
    await update.message.reply_text(text, reply_markup=markup)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()  # acknowledge the tap immediately, no artificial delay

    data = query.data
    log.info(f"Button pressed in chat {chat_id}: {data}")

    if data == "start_quiz":
        # Inert by design -- see start_command above. Q1 was already sent
        # unconditionally when /start was handled, before this tap could
        # even happen (the logs show Q1 arriving ~15s before the click
        # completed), so there's nothing left for this tap to trigger.
        # We don't have direct log evidence of what the real bot does on
        # this click server-side (only that no separate Q1 message
        # followed it) -- returning here without sending anything is the
        # closest match to what was actually observed.
        return

    if data.startswith("answer:"):
        _, q_index_str, chosen_str = data.split(":")
        q_index = int(q_index_str)
        chosen = int(chosen_str)

        session = _sessions.get(chat_id)
        if not session:
            await query.message.reply_text("Session expired -- send /start again.")
            return

        if chosen == session["questions"][q_index]["correct"]:
            session["correct_count"] += 1

        next_index = q_index + 1
        if next_index < TOTAL_QUESTIONS:
            text, markup = build_question_message(next_index, session["questions"])
            await query.message.reply_text(text, reply_markup=markup)
        else:
            # Quiz done -- report score and how long it took start-to-finish.
            # started_at is set when Q1 was sent (right after /start, see
            # start_command above), so this is the answering window from
            # Q1's actual delivery, not time spent reading the welcome
            # message or waiting on the (now-inert) Start Quiz tap.
            started_at = session.get("started_at")
            elapsed_seconds = time.monotonic() - started_at if started_at else None
            score = session["correct_count"]

            if elapsed_seconds is not None:
                time_str = f"{elapsed_seconds:.1f}s"
            else:
                time_str = "unknown (clock wasn't started)"

            log.info(
                f"Challenge complete in chat {chat_id}: score {score}/{TOTAL_QUESTIONS}, "
                f"time {time_str}"
            )
            await query.message.reply_text(
                "🏁 Challenge complete! (TEST)\n"
                f"Score: {score}/{TOTAL_QUESTIONS}\n"
                f"Time: {time_str}\n"
                "Thanks for testing."
            )
            _sessions.pop(chat_id, None)


async def run_for_a_while():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("post_join", post_join_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
        # Default long-poll timeout is 10s -- each getUpdates call blocks
        # for up to that long waiting on new updates before returning, which
        # is exactly the ~10s lag you'd see between /start and this bot's
        # rejection/response. Shortened so the activation-time retry loop
        # in run_challenge.py (which resends every couple seconds) actually
        # gets picked up promptly instead of being bottlenecked here.
        timeout=1,
    )
    log.info(f"Test bot is up and listening. Post to your test channel now. Will run for {RUN_MINUTES} minutes.")

    try:
        await asyncio.sleep(RUN_MINUTES * 60)
    finally:
        log.info("Time's up -- stopping test bot.")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    asyncio.run(run_for_a_while())


if __name__ == "__main__":
    main()
