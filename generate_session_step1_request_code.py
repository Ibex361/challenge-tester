"""
Step 1 of 2 for generating a new Telegram StringSession non-interactively
(e.g. on a GitHub Actions runner, which has no stdin for Telethon's usual
input() prompts). Run this via the "Generate TG Session (1/2: Request
Code)" workflow.

Requests a login code for PHONE_NUMBER and saves just enough intermediate
state (the not-yet-authorized session string, plus the phone_code_hash
Telegram returns) for step 2 to pick up and finish the login once you have
the code. This intermediate state is meaningless on its own -- it can't
answer messages or do anything as the account -- so it's fine to pass
between workflow runs as a plain build artifact rather than a secret; it
also naturally expires with the code (Telegram codes are short-lived).

Deliberately generic: takes any phone number, not tied to a specific
account name (SAF/ETH/Ab62/Ab82/etc) -- account naming only matters later,
when you decide which <ACCOUNT>_TG_SESSION secret to paste the final
string into.
"""
import asyncio
import json
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
PHONE_NUMBER = os.environ["PHONE_NUMBER"].strip()


async def main():
    if not PHONE_NUMBER:
        print("::error::PHONE_NUMBER input is empty.")
        sys.exit(1)

    # A fresh, empty StringSession -- nothing to log out of, nothing to
    # invalidate by running this multiple times for the same number.
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    sent = await client.send_code_request(PHONE_NUMBER)

    # session.save() at this point captures the DC info Telethon needs to
    # resume the SAME login attempt in step 2 -- reconnecting with a brand
    # new empty session in step 2 would not be able to complete this
    # specific pending login.
    intermediate_state = {
        "phone_number": PHONE_NUMBER,
        "phone_code_hash": sent.phone_code_hash,
        "session_string": client.session.save(),
    }
    with open("session_step1_state.json", "w") as f:
        json.dump(intermediate_state, f)

    await client.disconnect()

    print(f"Code requested for {PHONE_NUMBER}. Check the Telegram app itself first")
    print("(a message from the official 'Telegram' service account) -- SMS is only")
    print("a fallback after a short delay if the app isn't reachable.")
    print()
    print("Download this run's 'tg-session-step1-state' artifact, then run the")
    print("'Generate TG Session (2/2: Complete Login)' workflow with the code you")
    print("received (and your 2FA cloud password, if your account has one set).")
    print()
    print("This code/artifact expires soon -- if step 2 fails with an expired or")
    print("invalid code error, just re-run this step to request a fresh one.")


if __name__ == "__main__":
    asyncio.run(main())
