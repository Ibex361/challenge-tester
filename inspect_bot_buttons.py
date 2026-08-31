"""
Read-only diagnostic: inspect the button TYPES (callback vs URL) that the
challenge bot has actually sent, by scanning your private chat history with
it. Does NOT click anything, does NOT send any messages, does NOT touch the
channel. Safe to run any time, including against a closed/finished challenge.

Why: run_challenge.py assumes "Start Quiz" and the 5 answer-option buttons
are callback buttons (the only kind message.click() actually works for).
That assumption has never been directly verified -- only the Join button
was. This script checks it with real evidence instead of assumption.

Run this from the "Inspect Bot Buttons" workflow in the Actions tab (uses
the same repo secrets as the main challenge workflow) -- results appear
right on the job's summary page. Not meant to be run locally.
"""

import asyncio
import os

from telethon import TelegramClient
from telethon.sessions import StringSession
from datetime import datetime, timedelta, timezone

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_STRING = os.environ["TG_SESSION"]

BOT_USERNAME = os.environ["CHALLENGE_BOT_USERNAME"]
HISTORY_HOURS = float(os.environ.get("HISTORY_HOURS", "6"))

# When running in GitHub Actions, this points at a file whose contents get
# rendered as the job's summary page -- so results are readable right in the
# Actions UI, no log-scrolling or local files needed.
SUMMARY_PATH = os.environ.get("GITHUB_STEP_SUMMARY")


def describe_button(button) -> str:
    raw = getattr(button, "button", button)
    type_name = type(raw).__name__
    url = getattr(raw, "url", None)
    extra = f" url={url}" if url else ""
    return f"{type_name}{extra}"


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
    since = datetime.now(timezone.utc) - timedelta(hours=HISTORY_HOURS)

    emit([f"## Button type report for @{BOT_USERNAME}", "", f"Scanning last {HISTORY_HOURS}h of chat history.", ""])

    found_any_buttons = False
    async for message in client.iter_messages(bot_entity, limit=200):
        if message.date < since:
            break
        if not message.buttons:
            continue

        found_any_buttons = True
        preview = (message.text or "").strip().replace("\n", " ")[:60]
        block = [f"**[{message.date.isoformat()}]** `{preview}`", ""]
        for row_idx, row in enumerate(message.buttons):
            for col_idx, button in enumerate(row):
                label = (button.text or "").strip()
                block.append(f"- `({row_idx},{col_idx})` **{label}** -> `{describe_button(button)}`")
        block.append("")
        emit(block)

    if not found_any_buttons:
        emit(["_No buttoned messages found in that window. Try a larger HISTORY_HOURS input._"])

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
