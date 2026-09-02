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

# Needed only for the /post_join command (posting the "Join Challenge"
# message to your test channel on your behalf). TEST_CHANNEL_ID is the
# numeric channel ID (e.g. "-1001234567890"). TEST_ADMIN_USER_ID restricts
# who can trigger it -- your own numeric Telegram user ID (get it from
# @userinfobot). If TEST_ADMIN_USER_ID isn't set, /post_join is disabled
# entirely rather than left open to anyone who messages the bot.
TEST_CHANNEL_ID = os.environ.get("TEST_CHANNEL_ID")
TEST_ADMIN_USER_ID = os.environ.get("TEST_ADMIN_USER_ID")
TEST_BOT_USERNAME = os.environ.get("TEST_BOT_USERNAME", "birrforex_challenge_test_bot")

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
    # -- Style A: full option text on each button --
    {
        "text": "What does 'pip' stand for in forex trading?",
        "options": ["Percentage in Point", "Price in Points", "Profit in Position", "Point in Percentage"],
        "correct": 0,
        "bare_letters": False,
    },
    {
        "text": "Which currency pair is known as 'Cable'?",
        "options": ["EUR/USD", "USD/JPY", "GBP/USD", "USD/CHF"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": "A 'bullish' market means prices are generally:",
        "options": ["Falling", "Rising", "Flat", "Volatile with no trend"],
        "correct": 1,
        "bare_letters": False,
    },
    {
        "text": "Which of these is a 'safe haven' currency?",
        "options": ["Australian Dollar", "South African Rand", "Swiss Franc", "Turkish Lira"],
        "correct": 2,
        "bare_letters": False,
    },
    {
        "text": "What is 'leverage' in forex trading?",
        "options": [
            "A type of chart pattern",
            "Borrowed capital to increase position size",
            "The spread between bid and ask",
            "A central bank interest rate tool",
        ],
        "correct": 1,
        "bare_letters": False,
    },
    # -- Style B: options listed IN the question text, bare-letter buttons
    # (A/B/C/D only) -- matches your real bot's Question 2-4 style --
    {
        "text": (
            "Which of the following is NOT true about CFDs?\n"
            "A) They let you speculate on price without owning the asset\n"
            "B) They are only available for stock indices\n"
            "C) They can be traded on margin\n"
            "D) Profit or loss is based on the price difference"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "bare_letters": True,
    },
    {
        "text": (
            "Which of the following is NOT true about Futures contracts?\n"
            "A) They have a standardized contract size\n"
            "B) They are traded over-the-counter with no exchange\n"
            "C) They have a fixed expiration date\n"
            "D) They are used for hedging and speculation"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "bare_letters": True,
    },
    {
        "text": (
            "Which of the following is TRUE about futures contracts?\n"
            "A) They are traded on a regulated exchange\n"
            "B) They never have an expiration date\n"
            "C) They cannot be used for hedging\n"
            "D) They are identical to spot contracts"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 0,
        "bare_letters": True,
    },
    {
        "text": (
            "Which of the following is a leading global forex broker regulator?\n"
            "A) FIFA\n"
            "B) FCA (Financial Conduct Authority)\n"
            "C) WHO\n"
            "D) IATA"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "bare_letters": True,
    },
    {
        "text": (
            "What do we call selling an asset you do not currently own, "
            "expecting to buy it back later at a lower price?\n"
            "A) Going Short\n"
            "B) Hedging\n"
            "C) Selling on Margin\n"
            "D) Going Long"
        ),
        "options": ["A", "B", "C", "D"],
        "correct": 0,
        "bare_letters": True,
    },
]

TOTAL_QUESTIONS = 5
LETTERS = ["A", "B", "C", "D", "E", "F"]

# Per-chat quiz state: chat_id -> {"questions": [...], "index": int}
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
            "TEST_CHANNEL_ID isn't configured -- can't post. Set it to your test channel's numeric ID."
        )
        return

    # A fresh payload each time, so old deep links from a previous test
    # don't get confused with the current one in your run_challenge.py logs.
    payload = f"test_challenge_{int(random.random() * 1_000_000)}"
    deep_link = f"https://t.me/{TEST_BOT_USERNAME}?start={payload}"

    post_text = "📢 Join Challenge Now! (TEST)"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Join Challenge Now", url=deep_link)]])

    try:
        await context.bot.send_message(chat_id=TEST_CHANNEL_ID, text=post_text, reply_markup=keyboard)
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
    """
    chat_id = update.effective_chat.id
    payload = context.args[0] if context.args else "(none)"
    log.info(f"/start received from chat {chat_id}, payload={payload}")

    questions = random.sample(QUESTION_BANK, TOTAL_QUESTIONS)
    _sessions[chat_id] = {"questions": questions, "index": 0}

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
        text, markup = build_question_message(0, session["questions"])
        await query.message.reply_text(text, reply_markup=markup)
        return

    if data.startswith("answer:"):
        _, q_index_str, _chosen_str = data.split(":")
        q_index = int(q_index_str)

        session = _sessions.get(chat_id)
        if not session:
            await query.message.reply_text("Session expired -- send /start again.")
            return

        next_index = q_index + 1
        if next_index < TOTAL_QUESTIONS:
            text, markup = build_question_message(next_index, session["questions"])
            await query.message.reply_text(text, reply_markup=markup)
        else:
            await query.message.reply_text("🏁 Challenge complete! (TEST) Thanks for testing.")
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
