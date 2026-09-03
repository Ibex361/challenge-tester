"""
Automated challenge-flow tester.

Flow:
  1. Listen on your channel for a post that has a "Join Challenge" style button.
  2. Click it -> this opens/redirects to the challenge bot.
  3. Wait for a "Start Quiz" style button from the challenge bot, click it.
  4. For each of 5 questions: read question + options, ask Gemini which
     option is correct, click that option's button.
  5. Question 5's click is gated so it never lands sooner than
     MIN_SECONDS_SINCE_START_QUIZ seconds after the "Start Quiz" click.

Every stage logs clearly to stdout AND to the GitHub Actions job summary
(if running in Actions), so a failure is easy to locate.
"""

import asyncio
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.custom import Message

from google import genai
from google.genai import types as genai_types


# ----------------------------------------------------------------------
# Configuration (all from environment variables / GitHub Secrets)
# ----------------------------------------------------------------------

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_STRING = os.environ["TG_SESSION"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

TEST_MODE = os.environ.get("TEST_MODE", "false").lower() == "true"

# In test mode, we talk to the throwaway test_bot.py instead of the real
# challenge bot, and skip requiring a challenge number entirely -- test_bot.py
# doesn't validate the /start payload at all (see test_bot.py's start_command),
# so any fixed payload works.
if TEST_MODE:
    CHALLENGE_BOT_USERNAME = os.environ.get("TEST_BOT_USERNAME", "birrforex_challenge_test_bot")
    CHALLENGE_NUMBER = "test"
else:
    CHALLENGE_BOT_USERNAME = os.environ["CHALLENGE_BOT_USERNAME"]  # e.g. "birrforex_challenge_bot" (no @)
    CHALLENGE_NUMBER = os.environ["CHALLENGE_NUMBER"]              # e.g. "34" -> sends "/start challenge_34"

START_QUIZ_TEXT_HINTS = os.environ.get(
    "START_QUIZ_TEXT_HINTS", "start quiz,start"
).split(",")

TOTAL_QUESTIONS = int(os.environ.get("TOTAL_QUESTIONS", "5"))
MIN_SECONDS_SINCE_START_QUIZ = float(os.environ.get("MIN_SECONDS_SINCE_START_QUIZ", "15"))

# ---- Stage 1 timing (real mode only; test mode ignores all of this) ----
# The challenge bot rejects /start before it opens, replying with the exact
# text in CHALLENGE_NOT_ACTIVE_TEXT. In real mode we don't message the bot
# at all until CHALLENGE_OPEN_TIME_UTC, and we refuse to run entirely if
# started too early -- this script isn't meant to sit idle for a long time.
CHALLENGE_NOT_ACTIVE_TEXT = "This challenge is not active yet."
CHALLENGE_OPEN_TIME_UTC = os.environ.get("CHALLENGE_OPEN_TIME_UTC", "17:00")     # HH:MM, UTC
EARLIEST_RUN_TIME_UTC = os.environ.get("EARLIEST_RUN_TIME_UTC", "16:45")        # HH:MM, UTC
REAL_MODE_RETRY_DEADLINE_UTC = os.environ.get("REAL_MODE_RETRY_DEADLINE_UTC", "17:05")  # HH:MM, UTC
REAL_MODE_RETRY_INTERVAL_SECONDS = float(os.environ.get("REAL_MODE_RETRY_INTERVAL_SECONDS", "10"))

# Test mode has no clock -- just a flat time budget from whenever it starts.
TEST_MODE_TIMEOUT_MINUTES = float(os.environ.get("TEST_MODE_TIMEOUT_MINUTES", "10"))

# Once Start Quiz has been clicked, this is the separate time budget for the
# quiz-answering phase (Stage 3) -- independent of the Stage 1/2 gating above,
# since answering can legitimately run past the Stage 1/2 deadline once started.
QUIZ_TIMEOUT_MINUTES = float(os.environ.get("QUIZ_TIMEOUT_MINUTES", "15"))

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Temporary diagnostic switch: when true, logs EVERY incoming message across
# every chat (not just the ones we're filtering for), so we can see exactly
# what Telethon is receiving. Turn off once things are working reliably.
DEBUG_LOG_ALL_EVENTS = os.environ.get("DEBUG_LOG_ALL_EVENTS", "false").lower() == "true"


# ----------------------------------------------------------------------
# Small helpers for clear, stage-based logging
# ----------------------------------------------------------------------

SUMMARY_PATH = os.environ.get("GITHUB_STEP_SUMMARY")
_summary_lines = []


def log(stage: str, status: str, detail: str = ""):
    """
    status: one of "START", "OK", "FAIL", "INFO", "TIMEOUT"
    Prints to stdout immediately AND buffers a line for the job summary.
    """
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    symbol = {
        "START": "▶",
        "OK": "✅",
        "FAIL": "❌",
        "INFO": "ℹ️",
        "TIMEOUT": "⏰",
    }.get(status, "•")
    line = f"[{ts} UTC] {symbol} {stage}" + (f" — {detail}" if detail else "")
    print(line, flush=True)
    _summary_lines.append(f"| {ts} | {status} | {stage} | {detail} |")


def flush_summary(overall_result: str):
    if not SUMMARY_PATH:
        return
    with open(SUMMARY_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n## Challenge run result: {overall_result}\n\n")
        f.write("| Time (UTC) | Status | Stage | Detail |\n")
        f.write("|---|---|---|---|\n")
        f.write("\n".join(_summary_lines))
        f.write("\n")


class StageFailure(Exception):
    """Raised to stop the script with a clear stage name + reason."""
    def __init__(self, stage, detail):
        self.stage = stage
        self.detail = detail
        super().__init__(f"{stage}: {detail}")


def today_utc_at(hh_mm: str) -> datetime:
    """Parses 'HH:MM' into a UTC datetime for the current UTC calendar day."""
    hour, minute = (int(p) for p in hh_mm.split(":"))
    now = datetime.now(timezone.utc)
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ----------------------------------------------------------------------
# Gemini: ask which option letter is correct, with strict output + retry
# ----------------------------------------------------------------------

_gemini_client = genai.Client(api_key=GEMINI_API_KEY)

_LETTERS = ["A", "B", "C", "D", "E", "F"]  # supports up to 6 options, just in case


def _build_prompt(question_text: str, options: list[str]) -> str:
    lettered = "\n".join(f"{_LETTERS[i]}) {opt}" for i, opt in enumerate(options))
    return (
        "You are answering a multiple-choice question. "
        "Respond with ONLY the single letter of the correct option. "
        "No words, no punctuation, no explanation — just the letter.\n\n"
        f"Question: {question_text}\n\n"
        f"Options:\n{lettered}\n\n"
        "Answer (single letter only):"
    )


def ask_gemini_for_answer(question_text: str, options: list[str], attempt_label: str) -> str:
    """
    Returns the chosen option letter (e.g. "B"). Raises StageFailure if the
    model output can't be parsed into a valid option even after retry.
    """
    valid_letters = _LETTERS[: len(options)]

    # thinking_level="minimal" turns off Gemini 3's internal reasoning pass
    # for this call. max_output_tokens is capped hard since the schema-
    # constrained response is always a single letter -- this doesn't change
    # correctness, but it removes any chance of the model padding output
    # (e.g. restating the option text) and paying for tokens we discard.
    generation_config = genai_types.GenerateContentConfig(
        temperature=0,
        response_mime_type="text/x.enum",
        response_schema={"type": "STRING", "enum": valid_letters},
        thinking_config=genai_types.ThinkingConfig(thinking_level="minimal"),
        max_output_tokens=8,
        # No tools/functions are declared for this call -- explicitly turning
        # off automatic function calling avoids the SDK's unnecessary AFC
        # setup path (and the "not recommended" warning it logs).
        automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
    )

    prompt = _build_prompt(question_text, options)

    def _call_gemini():
        """
        One raw call to the API. Retries a couple of times on a transient
        exception (network blip, momentary API hiccup, etc.) before giving
        up -- this is about the call itself failing, separate from the
        call succeeding but returning text we can't parse (handled below).

        Backoff is short (0.5s) rather than a flat 2s: a Gemini ServerError
        is almost always a momentary hiccup that clears on the very next
        call, so there's no benefit to waiting longer, and every second
        here is a second added to every question in the quiz.
        """
        max_attempts = 3
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                return _gemini_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=generation_config,
                )
            except Exception as e:
                last_error = e
                if attempt < max_attempts:
                    log(
                        f"Gemini answer ({attempt_label})",
                        "INFO",
                        f"API call failed ({e.__class__.__name__}), retrying (attempt {attempt}/{max_attempts})",
                    )
                    time.sleep(0.5)
        raise StageFailure(
            f"Gemini answer ({attempt_label})",
            f"Gemini API call failed after {max_attempts} attempts: {last_error}",
        )


    def _try_once():
        resp = _call_gemini()
        text = (resp.text or "").strip().upper()
        match = re.search(r"[A-F]", text)
        return match.group(0) if match else None

    letter = _try_once()
    if letter in valid_letters:
        log(f"Gemini answer ({attempt_label})", "OK", f"chose {letter}")
        return letter

    log(f"Gemini answer ({attempt_label})", "INFO", f"unparseable response '{letter}', retrying once")
    letter = _try_once()
    if letter in valid_letters:
        log(f"Gemini answer ({attempt_label}, retry)", "OK", f"chose {letter}")
        return letter

    raise StageFailure(
        f"Gemini answer ({attempt_label})",
        f"could not get a valid option letter after retry (last raw value: {letter!r})",
    )


# ----------------------------------------------------------------------
# Telegram button helpers
# ----------------------------------------------------------------------

def describe_button(button) -> str:
    """
    Returns a short human-readable description of a Telethon Button's
    underlying type (URL button, callback button, etc.) — useful for
    diagnosing why a .click() didn't behave as expected.
    """
    # Telethon custom Button wraps a raw Telegram type in `.button`.
    raw = getattr(button, "button", button)
    type_name = type(raw).__name__
    extra = ""
    url = getattr(raw, "url", None)
    if url:
        extra = f" url={url}"
    return f"{type_name}{extra}"


def parse_telegram_deep_link(url: str):
    """
    Parses a t.me deep link of the form:
      https://t.me/<bot_username>?start=<payload>
      https://t.me/<bot_username>?startapp=<payload>
    Returns (bot_username, start_payload) or (None, None) if it doesn't
    match that shape (e.g. it's some other kind of link entirely).
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None, None

    if parsed.netloc not in ("t.me", "telegram.me"):
        return None, None

    bot_username = parsed.path.strip("/")
    if not bot_username:
        return None, None

    query = urllib.parse.parse_qs(parsed.query)
    payload = None
    for key in ("start", "startapp"):
        if key in query and query[key]:
            payload = query[key][0]
            break

    return bot_username, payload


async def click_button_or_follow_deep_link(client, message, row, col, stage_name):
    """
    Clicks a button the way a real user tap would behave, handling both
    button kinds correctly:
      - Callback buttons: message.click() works as normal — it submits the
        callback to Telegram, and the bot reacts server-side.
      - URL buttons pointing at a t.me/<bot>?start=<payload> deep link:
        message.click() does NOT replicate a real tap for these — it just
        returns the URL and does nothing further. A real tap opens a chat
        with that bot and sends "/start <payload>" as a message. We
        replicate that explicitly: resolve the bot and send that command
        ourselves.
      - Any other URL (not a recognized bot deep link): we can't safely
        automate arbitrary link-opening, so this raises a clear failure
        rather than silently doing nothing.
    Returns the bot entity that should be used for the rest of the flow.
    """
    button = message.buttons[row][col]
    raw = getattr(button, "button", button)
    url = getattr(raw, "url", None)

    if url:
        bot_username, payload = parse_telegram_deep_link(url)
        if bot_username is None:
            raise StageFailure(
                stage_name,
                f"button is a URL button but not a recognized bot deep link ({url}); can't automate this safely",
            )

        log(stage_name, "INFO", f"URL button detected -> deep link to @{bot_username} with payload '{payload}'; replicating a real tap by sending /start")
        bot_entity = await client.get_entity(bot_username)
        start_command = f"/start {payload}" if payload else "/start"
        await client.send_message(bot_entity, start_command)
        log(stage_name, "OK", f"sent '{start_command}' to @{bot_username}")
        return bot_entity

    # Not a URL button -> normal callback button, .click() is correct here.
    await message.click(row, col)
    log(stage_name, "OK", "clicked callback button")
    return None


def find_button_by_hints(message: Message, hints: list[str]):
    """
    message.buttons is a 2D list of Telethon Button objects.
    Returns (row, col) of the first button whose text matches one of the
    hints (case-insensitive substring match), or None.
    """
    if not message.buttons:
        return None
    for row_idx, row in enumerate(message.buttons):
        for col_idx, button in enumerate(row):
            label = (button.text or "").strip().lower()
            for hint in hints:
                if hint.strip().lower() in label:
                    return row_idx, col_idx
    return None


def extract_mcq_options(message: Message) -> list[str]:
    """
    Pulls option labels straight off the inline buttons (A / B / C / D),
    matching your challenge bot's format where the button itself is the
    answer choice.
    """
    if not message.buttons:
        return []
    options = []
    for row in message.buttons:
        for button in row:
            options.append((button.text or "").strip())
    return options


# ----------------------------------------------------------------------
# Main flow
# ----------------------------------------------------------------------

async def wait_for_event_with_deadline(client, event_builder, deadline_dt, stage_name):
    """
    Waits for a single matching event, but gives up at deadline_dt.
    Returns the event, or raises StageFailure on timeout.
    """
    remaining = (deadline_dt - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise StageFailure(stage_name, "deadline already passed")

    fut = asyncio.get_event_loop().create_future()

    async def handler(event):
        if not fut.done():
            fut.set_result(event)

    client.add_event_handler(handler, event_builder)
    try:
        log(stage_name, "START", f"waiting up to {int(remaining)}s")
        event = await asyncio.wait_for(fut, timeout=remaining)
        log(stage_name, "OK", "message received")
        return event
    except asyncio.TimeoutError:
        log(stage_name, "TIMEOUT", f"no matching message before deadline ({deadline_dt.isoformat()})")
        raise StageFailure(stage_name, "timed out waiting for message")
    finally:
        client.remove_event_handler(handler, event_builder)


async def message_bot_with_retry_until_active(
    client, challenge_bot, start_command, hints, deadline_dt, retry_interval_seconds, stage_name,
):
    """
    Sends `start_command` to the bot, then waits for its reply. Some bots
    reply immediately with a rejection (e.g. "This challenge is not active
    yet.") if messaged too early -- if that happens, this waits
    `retry_interval_seconds` and sends the command again, repeating until
    either a message with a matching button (`hints`) arrives, or
    `deadline_dt` passes.

    Returns (message, (row, col)) for the matched button.
    """
    result_fut = asyncio.get_event_loop().create_future()

    async def handler(event):
        if result_fut.done():
            return
        msg = event.message
        loc = find_button_by_hints(msg, hints)
        if loc is not None:
            result_fut.set_result((msg, loc))
            return
        text = (msg.text or "").strip()
        preview = text.replace("\n", " ")[:60]
        if CHALLENGE_NOT_ACTIVE_TEXT in text:
            log(stage_name, "INFO", "bot reports the challenge is not active yet -- will retry")
        else:
            log(stage_name, "INFO", f"message received without matching button, still waiting (preview: '{preview}')")

    client.add_event_handler(handler, events.NewMessage(chats=challenge_bot))
    try:
        attempt = 1
        while True:
            remaining = (deadline_dt - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                log(stage_name, "TIMEOUT", f"no matching message before deadline ({deadline_dt.isoformat()})")
                raise StageFailure(stage_name, "timed out waiting for a message with the expected button")

            await client.send_message(challenge_bot, start_command)
            log(stage_name, "INFO", f"sent '{start_command}' (attempt {attempt})")

            wait_for = min(remaining, retry_interval_seconds)
            try:
                msg, loc = await asyncio.wait_for(asyncio.shield(result_fut), timeout=wait_for)
                log(stage_name, "OK", f"found matching button at row {loc[0]}, col {loc[1]} (attempt {attempt})")
                return msg, loc
            except asyncio.TimeoutError:
                attempt += 1
                continue
    finally:
        client.remove_event_handler(handler, events.NewMessage(chats=challenge_bot))


async def main():
    now = datetime.now(timezone.utc)

    if TEST_MODE:
        # No clock gating at all -- contact the bot immediately, give up
        # after a flat time budget from right now.
        deadline = now + timedelta(minutes=TEST_MODE_TIMEOUT_MINUTES)
        open_time = None
    else:
        earliest_run_time = today_utc_at(EARLIEST_RUN_TIME_UTC)
        open_time = today_utc_at(CHALLENGE_OPEN_TIME_UTC)
        deadline = today_utc_at(REAL_MODE_RETRY_DEADLINE_UTC)

        if now < earliest_run_time:
            raise StageFailure(
                "Startup check",
                f"it's {now.strftime('%H:%M:%S')} UTC, which is before {EARLIEST_RUN_TIME_UTC} UTC "
                f"({EARLIEST_RUN_TIME_UTC} is the earliest this is allowed to start). "
                f"Trigger the workflow again closer to {CHALLENGE_OPEN_TIME_UTC} UTC.",
            )

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start()
    log("Telegram login", "OK", "session authenticated")

    try:
        challenge_bot = await client.get_entity(CHALLENGE_BOT_USERNAME)
        mode_note = " (TEST MODE)" if TEST_MODE else ""
        log("Resolve bot", "INFO", f"resolved '@{CHALLENGE_BOT_USERNAME}' -> id={challenge_bot.id}{mode_note}")

        if DEBUG_LOG_ALL_EVENTS:
            async def _debug_any_event(event):
                msg = event.message
                chat = await event.get_chat()
                chat_id = getattr(chat, "id", "?")
                chat_title = getattr(chat, "title", getattr(chat, "username", "?"))
                has_buttons = bool(msg.buttons)
                preview = (msg.text or "").strip().replace("\n", " ")[:60]
                log("DEBUG any event", "INFO",
                    f"chat_id={chat_id} title='{chat_title}' has_buttons={has_buttons} preview='{preview}'")
            client.add_event_handler(_debug_any_event, events.NewMessage())
            log("DEBUG mode", "INFO", "logging ALL incoming events (any chat) alongside normal stages")

        # Login and bot resolution are done above, BEFORE this sleep, so the
        # very first "/start challenge_<N>" send below happens as close to
        # CHALLENGE_OPEN_TIME_UTC as possible -- not delayed by connecting
        # to Telegram or resolving the bot entity after the clock hits it.
        if not TEST_MODE and now < open_time:
            sleep_seconds = (open_time - datetime.now(timezone.utc)).total_seconds()
            if sleep_seconds > 0:
                log("Startup check", "INFO",
                    f"logged in and ready; waiting {int(sleep_seconds)}s until "
                    f"{CHALLENGE_OPEN_TIME_UTC} UTC before messaging the bot")
                await asyncio.sleep(sleep_seconds)
        elif not TEST_MODE:
            log("Startup check", "INFO",
                f"started at {now.strftime('%H:%M:%S')} UTC, at/after {CHALLENGE_OPEN_TIME_UTC} UTC -- messaging the bot now")

        # ---- Stage 1 + 2: message the bot, retry if not active yet, wait for Start Quiz ----
        # No more listening on any channel -- we go straight to the bot the
        # same way a real tap on the channel's Join button would have,
        # replicating exactly what click_button_or_follow_deep_link() did
        # for a URL button: send "/start challenge_<N>" to the bot.
        #
        # In real mode the bot may reply "This challenge is not active yet."
        # if we're a beat early -- we keep resending until either the
        # "Start Quiz" button shows up or the deadline passes. In test mode
        # the test bot never rejects, so this resolves on the first attempt.
        start_command = f"/start challenge_{CHALLENGE_NUMBER}"
        retry_interval = 3.0 if TEST_MODE else REAL_MODE_RETRY_INTERVAL_SECONDS
        start_quiz_message, loc = await message_bot_with_retry_until_active(
            client,
            challenge_bot,
            start_command,
            START_QUIZ_TEXT_HINTS,
            deadline,
            retry_interval,
            "Message bot / wait for Start Quiz",
        )

        await click_button_or_follow_deep_link(client, start_quiz_message, loc[0], loc[1], "Click Start Quiz")
        start_quiz_click_time = time.monotonic()
        log("Click Start Quiz", "OK", f"timer started")

        # From here on, use a fresh deadline for the quiz-answering phase --
        # it must not be truncated to whatever time was left on the Stage
        # 1/2 gating deadline above (e.g. 17:05 UTC in real mode), since the
        # quiz itself can legitimately run past that clock time once started.
        quiz_deadline = datetime.now(timezone.utc) + timedelta(minutes=QUIZ_TIMEOUT_MINUTES)

        # ---- Stage 3: answer each question ----
        for q_num in range(1, TOTAL_QUESTIONS + 1):
            stage = f"Question {q_num}/{TOTAL_QUESTIONS}"

            q_event = await wait_for_event_with_deadline(
                client,
                events.NewMessage(chats=challenge_bot),
                quiz_deadline,
                f"{stage}: wait for question",
            )
            q_message: Message = q_event.message

            options = extract_mcq_options(q_message)
            if not options:
                raise StageFailure(stage, "message received but no answer buttons found")

            question_text = q_message.text or ""
            log(stage, "INFO", f"parsed {len(options)} options")

            # ask_gemini_for_answer() is a blocking, synchronous call (network
            # I/O + time.sleep on retry). Run it in a worker thread so it
            # doesn't freeze this event loop -- otherwise Telethon can't
            # process anything else (including the eventual button click)
            # until the call returns, which is what caused the apparent
            # "stall" on Question 4.
            answer_letter = await asyncio.to_thread(
                ask_gemini_for_answer, question_text, options, stage
            )
            answer_index = _LETTERS.index(answer_letter)
            answer_text = options[answer_index] if answer_index < len(options) else "?"
            log(stage, "INFO", f"Gemini's answer: {answer_letter}) {answer_text}")

            if q_num == TOTAL_QUESTIONS:
                elapsed = time.monotonic() - start_quiz_click_time
                if elapsed < MIN_SECONDS_SINCE_START_QUIZ:
                    wait_for = MIN_SECONDS_SINCE_START_QUIZ - elapsed
                    log(stage, "INFO", f"pacing: waiting {wait_for:.1f}s before final click")
                    await asyncio.sleep(wait_for)

            # Buttons were flattened row-by-row in extract_mcq_options; map
            # the flat index back to (row, col) for the click.
            flat_idx = 0
            clicked = False
            for row_idx, row in enumerate(q_message.buttons):
                for col_idx, _ in enumerate(row):
                    if flat_idx == answer_index:
                        await click_button_or_follow_deep_link(client, q_message, row_idx, col_idx, stage)
                        clicked = True
                        break
                    flat_idx += 1
                if clicked:
                    break

            if not clicked:
                raise StageFailure(stage, "failed to map answer letter to a button position")

            log(stage, "OK", f"clicked option {answer_letter}")

        log("Challenge flow", "OK", "all questions answered")
        flush_summary("SUCCESS ✅")

    except StageFailure as e:
        log(e.stage, "FAIL", e.detail)
        flush_summary(f"FAILED at: {e.stage} — {e.detail}")
        sys.exit(1)
    except Exception as e:
        log("Unexpected error", "FAIL", str(e))
        flush_summary(f"FAILED (unexpected) — {e}")
        raise
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
