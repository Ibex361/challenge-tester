"""
Step 2 of 2 for generating a new Telegram StringSession non-interactively.
Run this via the "Generate TG Session (2/2: Complete Login)" workflow,
after downloading the artifact step 1 produced and receiving your login
code from Telegram.

Completes the pending login started in step 1 (using its saved
phone_code_hash + intermediate session) and prints the final, fully
authorized StringSession string -- copy that into whichever
<ACCOUNT>_TG_SESSION repo secret you're setting up (e.g. AB62_TG_SESSION).

If the account has Two-Step Verification (a cloud password) enabled,
Telethon will ask for it after the code is accepted -- TG_PASSWORD is
optional for exactly that case; leave it unset/blank if the account
doesn't use 2FA.
"""
import asyncio
import json
import os
import sys

from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
CODE = os.environ["TG_CODE"].strip()
PASSWORD = os.environ.get("TG_PASSWORD", "").strip() or None


async def main():
    if not CODE:
        print("::error::TG_CODE input is empty.")
        sys.exit(1)

    try:
        with open("session_step1_state.json") as f:
            state = json.load(f)
    except FileNotFoundError:
        print(
            "::error::session_step1_state.json not found -- did you download and "
            "attach the 'tg-session-step1-state' artifact from step 1's run?"
        )
        sys.exit(1)

    phone_number = state["phone_number"]
    phone_code_hash = state["phone_code_hash"]
    client = TelegramClient(StringSession(state["session_string"]), API_ID, API_HASH)
    await client.connect()

    try:
        await client.sign_in(
            phone=phone_number,
            code=CODE,
            phone_code_hash=phone_code_hash,
        )
    except SessionPasswordNeededError:
        if not PASSWORD:
            print(
                "::error::This account has Two-Step Verification enabled -- "
                "re-run this workflow with the tg_password input set to your "
                "cloud password."
            )
            await client.disconnect()
            sys.exit(1)
        await client.sign_in(password=PASSWORD)
    except PhoneCodeInvalidError:
        print("::error::That code was rejected as invalid. Double-check it and retry.")
        await client.disconnect()
        sys.exit(1)
    except PhoneCodeExpiredError:
        print(
            "::error::That code has expired -- re-run step 1 "
            "('Generate TG Session (1/2: Request Code)') to request a fresh one."
        )
        await client.disconnect()
        sys.exit(1)

    final_session_string = client.session.save()
    await client.disconnect()

    # Printed plainly in the run log, per explicit choice (2026-09-13) --
    # anyone with read access to this repo's Actions logs can see it, so
    # only use this on a repo where that's acceptable, and copy the string
    # into the target <ACCOUNT>_TG_SESSION secret promptly rather than
    # leaving it to sit only in the log.
    print()
    print("Login complete. Final session string (copy this into the target")
    print("<ACCOUNT>_TG_SESSION secret, e.g. AB62_TG_SESSION):")
    print()
    print(final_session_string)


if __name__ == "__main__":
    asyncio.run(main())
