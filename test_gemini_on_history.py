"""
Read-only diagnostic: replay historical quiz questions (Question 1-5
messages from the challenge bot) through the REAL Gemini answering logic
from run_challenge.py, and report what letter + option text it would have
chosen. Does NOT click anything and does NOT send anything to the bot --
this only reads chat history and calls Gemini.

Why: with the challenge closed, buttons can't be usefully clicked live
anymore, but you can still verify Gemini's answering behavior against real
past questions -- same prompt, same model, same parsing/retry logic that
the live flow uses (imported directly from run_challenge.py, not
reimplemented), just without the Telegram click at the end.

Run this from the "Test Gemini On History" workflow in the Actions tab
(uses the same repo secrets as the other workflows) -- results appear on
the job's summary page. Not meant to be run locally.
"""

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.sessions import StringSession

# Reuses the exact same prompt-building, model call, parsing and retry
# logic the live flow uses -- so this tests the real thing, not a copy of it.
from run_challenge import ask_gemini_for_answer, extract_mcq_options, StageFailure

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_STRING = os.environ["TG_SESSION"]

BOT_USERNAME = os.environ["CHALLENGE_BOT_USERNAME"]
HISTORY_HOURS = float(os.environ.get("HISTORY_HOURS", "6"))
ON_DATE_RAW = os.environ.get("ON_DATE", "").strip()

# Only messages whose text starts with "Question N/M" are treated as
# quiz questions -- matches your bot's actual message format seen in the
# button-inspection report (e.g. "Question 5/5 ⏱️ ...").
QUESTION_PREFIX_RE = re.compile(r"^Question\s+\d+/\d+", re.IGNORECASE)

SUMMARY_PATH = os.environ.get("GITHUB_STEP_SUMMARY")


def resolve_window() -> tuple[datetime, datetime, str]:
    """
    Returns (window_start_utc, window_end_utc, description_for_report).
    If ON_DATE is set, the window is exactly that one UTC calendar day.
    Otherwise falls back to "the last HISTORY_HOURS hours, up to now".
    """
    if ON_DATE_RAW:
        try:
            day = datetime.fromisoformat(ON_DATE_RAW).date()
        except ValueError:
            raise ValueError(
                f"Couldn't parse ON_DATE='{ON_DATE_RAW}'. Use the format 'YYYY-MM-DD', e.g. '2026-08-26'."
            )
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1) - timedelta(microseconds=1)
        return start, end, f"messages on {day.isoformat()} (UTC), from ON_DATE='{ON_DATE_RAW}'"

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=HISTORY_HOURS)
    return start, end, f"messages from the last {HISTORY_HOURS}h"


def emit(lines: list[str]):
    """Print to the normal job log AND append to the Actions job summary."""
    text = "\n".join(lines)
    print(text)
    if SUMMARY_PATH:
        with open(SUMMARY_PATH, "a") as f:
            f.write(text + "\n")


async def main():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start()

    bot_entity = await client.get_entity(BOT_USERNAME)
    window_start, window_end, description = resolve_window()

    emit([f"## Gemini answer replay for @{BOT_USERNAME}", "", f"Scanning {description}.", "",
          "_Diagnostic only -- nothing is clicked or sent to the bot._", ""])

    # Collect matching question messages, then replay oldest-first so the
    # report reads in the same order the quiz was actually taken.
    question_messages = []
    async for message in client.iter_messages(bot_entity, limit=200):
        if message.date > window_end:
            continue
        if message.date < window_start:
            break
        text = (message.text or "").strip()
        if QUESTION_PREFIX_RE.match(text) and message.buttons:
            question_messages.append(message)
    question_messages.reverse()

    if not question_messages:
        emit(["_No question messages found in that window. Try a different ON_DATE or a larger HISTORY_HOURS._"])
        await client.disconnect()
        return

    for message in question_messages:
        question_text = message.text or ""
        options = extract_mcq_options(message)
        title = question_text.strip().split("\n")[0][:80]

        block = [f"**[{message.date.isoformat()}]** `{title}`", ""]
        try:
            letter = ask_gemini_for_answer(question_text, options, title)
            letters = ["A", "B", "C", "D", "E", "F"]
            chosen_text = options[letters.index(letter)] if letter in letters[: len(options)] else "?"
            block.append(f"- Gemini would answer: **{letter}) {chosen_text}**")
        except StageFailure as e:
            block.append(f"- ⚠️ Gemini failed to answer: {e.detail}")
        block.append("")
        emit(block)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
