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
# Random forex question bank -- for testing Gemini's answering ability,
# not real challenge content. Each question has exactly one correct
# option; a fresh set of 5 is picked at random per test session so
# answers aren't memorized/stale across runs.
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Random forex question bank -- for testing Gemini's answering ability,
# not real challenge content. Each question has exactly one correct
# option; a fresh set of 5 is picked at random per test session so
# answers aren't memorized/stale across runs.
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

QUESTION_BANK = [
    # ------------------------------------------------------------------
    # Real questions captured from the LIVE challenge bot (screenshots,
    # 2026-09-04), cross-checked against the user's official answer key
    # (also 2026-09-04). Hand-written placeholder questions have been
    # removed -- this bank is now 100% real captured questions, matching
    # actual live difficulty rather than an approximation of it.
    # ------------------------------------------------------------------

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

    # -- Section 5: Understanding Currency Pairs (Trading Instruments) --
    # (the 2 video-referencing questions from this section live in
    # VIDEO_REFERENCE_QUESTION_BANK below, not here)
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

    # -- Section 2: CFD (Contract for Difference) --
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

# ----------------------------------------------------------------------
# Video-reference questions -- from Section 5, explicitly point at "the
# Section Video" for the answer (e.g. "in the USDJPY Example given in
# the Section Video, what is the Spread"). The AI answering the quiz has
# no access to that video, so these are UNANSWERABLE from the question
# text alone -- kept OUT of the normal random QUESTION_BANK draw (they'd
# just be unfairly-unanswerable noise most of the time) and instead only
# appear when explicitly requested, so you can specifically test how the
# AI provider behaves when it hits a question it fundamentally can't
# answer (e.g. does it pick something plausible, refuse, time out, etc).
# Correct answers below are the user's official answer key -- used for
# scoring only, NOT given to the AI, which still has to guess blind.
# See TEST_INCLUDE_VIDEO_QUESTIONS env var / workflow input.
# ----------------------------------------------------------------------
VIDEO_REFERENCE_QUESTION_BANK = [
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

TOTAL_QUESTIONS = 5
LETTERS = ["A", "B", "C", "D", "E", "F"]

# Set TEST_INCLUDE_VIDEO_QUESTIONS=true to guarantee both
# VIDEO_REFERENCE_QUESTION_BANK questions are included among the 5 for
# every session this run -- lets you deliberately test the
# unanswerable-question scenario on demand instead of waiting for a rare
# random draw. Off by default so ordinary test runs behave as before.
INCLUDE_VIDEO_QUESTIONS = os.environ.get("TEST_INCLUDE_VIDEO_QUESTIONS", "false").lower() == "true"

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

    if INCLUDE_VIDEO_QUESTIONS:
        # Guarantee both video-reference questions appear, then fill the
        # rest randomly from the normal bank so the session is still 5
        # questions total and still varies run to run.
        remaining_slots = TOTAL_QUESTIONS - len(VIDEO_REFERENCE_QUESTION_BANK)
        questions = list(VIDEO_REFERENCE_QUESTION_BANK) + random.sample(QUESTION_BANK, remaining_slots)
        random.shuffle(questions)
        log.info("TEST_INCLUDE_VIDEO_QUESTIONS is set -- both video-reference questions included this session")
    else:
        questions = random.sample(QUESTION_BANK, TOTAL_QUESTIONS)
    _sessions[chat_id] = {
        "questions": questions,
        "index": 0,
        "started_at": None,  # set when START QUIZ is tapped and Q1 goes out
        "correct_count": 0,
    }

    welcome_text = (
        "📚 Welcome to My Personal Challenge Guys! 📊\n"
        "Section: Forex Basics (TEST)\n\n"
        "Tap below when you're ready to begin."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("START QUIZ", callback_data="start_quiz")]])
    await update.message.reply_text(welcome_text, reply_markup=keyboard)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()  # acknowledge the tap, same as a real bot would

    data = query.data
    log.info(f"Button pressed in chat {chat_id}: {data}")

    if data == "start_quiz":
        session = _sessions.get(chat_id)
        if not session:
            await query.message.reply_text("Session expired -- send /start again.")
            return
        session["started_at"] = time.monotonic()  # quiz clock starts as Q1 goes out
        text, markup = build_question_message(0, session["questions"])
        await query.message.reply_text(text, reply_markup=markup)
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
            # started_at is set when Q1 went out (START QUIZ tap), so this
            # is exactly the answering window, not time spent on /start
            # or reading the welcome message.
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
