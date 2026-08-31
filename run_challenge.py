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

import google.generativeai as genai


# ----------------------------------------------------------------------
# Configuration (all from environment variables / GitHub Secrets)
# ----------------------------------------------------------------------

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_STRING = os.environ["TG_SESSION"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

CHANNEL_USERNAME = os.environ["CHANNEL_USERNAME"]          # your channel, e.g. "my_channel"
JOIN_BUTTON_TEXT_HINTS = os.environ.get(
    "JOIN_BUTTON_TEXT_HINTS", "join challenge,join,challenge"
).split(",")
START_QUIZ_TEXT_HINTS = os.environ.get(
    "START_QUIZ_TEXT_HINTS", "start quiz,start"
).split(",")

TOTAL_QUESTIONS = int(os.environ.get("TOTAL_QUESTIONS", "5"))
MIN_SECONDS_SINCE_START_QUIZ = float(os.environ.get("MIN_SECONDS_SINCE_START_QUIZ", "17"))

# Overall deadline: the workflow starts the process; this env var tells the
# script the wall-clock UTC time it must give up by (ISO 8601). Falls back
# to "listen for 15 minutes from now" if not provided, so local testing works.
DEADLINE_UTC_ISO = os.environ.get("DEADLINE_UTC_ISO")

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

# Temporary diagnostic switch: when true, logs EVERY incoming message across
# every chat (not just the ones we're filtering for), so we can see exactly
# what Telethon is receiving. Turn off once things are working reliably.
DEBUG_LOG_ALL_EVENTS = os.environ.get("DEBUG_LOG_ALL_EVENTS", "false").lower() == "true"

# Separate one-off diagnostic mode: instead of listening live going forward,
# scan the channel's recent message HISTORY for a message with a matching
# Join button, posted within the last N hours. Useful for testing against a
# challenge you already posted earlier, without needing to be live for it.
# This does NOT touch the real flow's live-listening logic at all — it's a
# standalone diagnostic path.
DEBUG_SCAN_HISTORY_HOURS = float(os.environ.get("DEBUG_SCAN_HISTORY_HOURS", "0"))


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


# ----------------------------------------------------------------------
# Gemini: ask which option letter is correct, with strict output + retry
# ----------------------------------------------------------------------

genai.configure(api_key=GEMINI_API_KEY)

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

    model = genai.GenerativeModel(
        GEMINI_MODEL,
        generation_config={
            "temperature": 0,
            "response_mime_type": "text/x.enum",
            "response_schema": {"type": "STRING", "enum": valid_letters},
        },
    )

    prompt = _build_prompt(question_text, options)

    def _try_once():
        resp = model.generate_content(prompt)
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


async def wait_for_message_with_button(client, event_builder, hints, deadline_dt, stage_name):
    """
    Some bots send several messages in a row (e.g. a plain welcome message,
    THEN a separate message with the button we actually want). This keeps
    listening — across multiple incoming messages if needed — until one of
    them has a button matching `hints`, or the deadline passes.

    Returns (message, (row, col)) for the matched button.
    """
    remaining = (deadline_dt - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise StageFailure(stage_name, "deadline already passed")

    result_fut = asyncio.get_event_loop().create_future()

    async def handler(event):
        msg = event.message
        loc = find_button_by_hints(msg, hints)
        if loc is not None and not result_fut.done():
            result_fut.set_result((msg, loc))
        else:
            # Message arrived but didn't have the button we want yet —
            # log it and keep waiting for the next one.
            preview = (msg.text or "").strip().replace("\n", " ")[:60]
            log(stage_name, "INFO", f"message received without matching button, still waiting (preview: '{preview}')")

    client.add_event_handler(handler, event_builder)
    try:
        log(stage_name, "START", f"waiting up to {int(remaining)}s")
        msg, loc = await asyncio.wait_for(result_fut, timeout=remaining)
        log(stage_name, "OK", f"found matching button at row {loc[0]}, col {loc[1]}")
        return msg, loc
    except asyncio.TimeoutError:
        log(stage_name, "TIMEOUT", f"no message with a matching button before deadline ({deadline_dt.isoformat()})")
        raise StageFailure(stage_name, "timed out waiting for a message with the expected button")
    finally:
        client.remove_event_handler(handler, event_builder)


async def scan_history_for_button(client, chat_entity, hints, hours, stage_name):
    """
    One-off diagnostic: scans the chat's recent message history (not live
    events) for a message with a button matching `hints`, posted within the
    last `hours` hours. Returns (message, (row, col)) or raises StageFailure.

    This is ONLY for manual debugging against a challenge already posted
    earlier — the real flow always uses live listening, never history scans,
    because the bot's private quiz messages can't be scanned this way.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    log(stage_name, "START", f"scanning history back to {cutoff.isoformat()}")

    checked = 0
    async for msg in client.iter_messages(chat_entity, offset_date=None, limit=200):
        if msg.date < cutoff:
            break
        checked += 1
        loc = find_button_by_hints(msg, hints)
        if loc is not None:
            log(stage_name, "OK", f"found matching button in message from {msg.date.isoformat()} (checked {checked} messages)")
            return msg, loc
        preview = (msg.text or "").strip().replace("\n", " ")[:60]
        log(stage_name, "INFO", f"message from {msg.date.isoformat()} has no matching button (preview: '{preview}')")

    raise StageFailure(stage_name, f"no message with a matching button found in the last {hours}h ({checked} messages checked)")


async def main():
    if DEADLINE_UTC_ISO:
        deadline = datetime.fromisoformat(DEADLINE_UTC_ISO)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
    else:
        deadline = datetime.now(timezone.utc) + timedelta(minutes=15)

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start()
    log("Telegram login", "OK", "session authenticated")

    try:
        channel_entity = await client.get_entity(CHANNEL_USERNAME)
        log("Resolve channel", "INFO", f"resolved '{CHANNEL_USERNAME}' -> id={channel_entity.id}, title={getattr(channel_entity, 'title', '?')}")

        # ---- Diagnostic-only path: scan history instead of listening live ----
        if DEBUG_SCAN_HISTORY_HOURS > 0:
            log("DEBUG mode", "INFO", f"scanning last {DEBUG_SCAN_HISTORY_HOURS}h of channel history instead of listening live")
            join_message, loc = await scan_history_for_button(
                client, channel_entity, JOIN_BUTTON_TEXT_HINTS,
                DEBUG_SCAN_HISTORY_HOURS, "Scan channel history for Join button",
            )
            log("DEBUG history scan", "OK", "found the Join button in recent history — triggering the join action now")
            await click_button_or_follow_deep_link(client, join_message, loc[0], loc[1], "Click join button")
            log("DEBUG mode", "INFO", "history-scan diagnostic complete; continuing with LIVE listening from here for the rest of the flow")
            # Falls through to the normal live-listening flow below for
            # stage 2 onward, since the bot's quiz messages can't be
            # history-scanned this way (they haven't been sent yet at
            # scan time).

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

        # ---- Stage 1: wait for the channel post with the Join button ----
        # (skipped if the debug history scan above already found and clicked it)
        if DEBUG_SCAN_HISTORY_HOURS <= 0:
            join_message, loc = await wait_for_message_with_button(
                client,
                events.NewMessage(chats=channel_entity),
                JOIN_BUTTON_TEXT_HINTS,
                deadline,
                "Wait for channel post with Join button",
            )
            await click_button_or_follow_deep_link(client, join_message, loc[0], loc[1], "Click join button")

        # ---- Stage 2: wait for "Start Quiz" from the challenge bot ----
        # We listen to ALL new incoming private messages, since we don't know
        # the challenge bot's username until it messages us. The bot may send
        # a plain "Welcome" message FIRST with no button, then a separate
        # message with the "START QUIZ" button — so we keep listening across
        # messages until one of them actually has the button we want.
        start_quiz_message, loc = await wait_for_message_with_button(
            client,
            events.NewMessage(incoming=True),
            START_QUIZ_TEXT_HINTS,
            deadline,
            "Wait for Start Quiz prompt",
        )
        challenge_bot = await start_quiz_message.get_sender()

        await click_button_or_follow_deep_link(client, start_quiz_message, loc[0], loc[1], "Click Start Quiz")
        start_quiz_click_time = time.monotonic()
        log("Click Start Quiz", "OK", f"timer started")

        # ---- Stage 3: answer each question ----
        for q_num in range(1, TOTAL_QUESTIONS + 1):
            stage = f"Question {q_num}/{TOTAL_QUESTIONS}"

            q_event = await wait_for_event_with_deadline(
                client,
                events.NewMessage(chats=challenge_bot),
                deadline,
                f"{stage}: wait for question",
            )
            q_message: Message = q_event.message

            options = extract_mcq_options(q_message)
            if not options:
                raise StageFailure(stage, "message received but no answer buttons found")

            question_text = q_message.text or ""
            log(stage, "INFO", f"parsed {len(options)} options")

            answer_letter = ask_gemini_for_answer(question_text, options, stage)
            answer_index = _LETTERS.index(answer_letter)

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
