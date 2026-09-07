"""
Automated challenge-flow tester.

Flow:
  1. Listen on your channel for a post that has a "Join Challenge" style button.
  2. Click it -> this opens/redirects to the challenge bot.
  3. Wait for a "Start Quiz" style button from the challenge bot, click it.
  4. For each of 5 questions: read question + options, ask Gemini which
     option is correct, click that option's button.
  5. Each question's click is paced: it never lands sooner than that
     question's configured floor (control/pacing_SAF.json or
     control/pacing_ETH.json, per the selected account) after the
     question's message was received, so no question gets answered
     near-instantly.

Every stage logs clearly to stdout AND to the GitHub Actions job summary
(if running in Actions), so a failure is easy to locate.
"""

import asyncio
import json
import logging
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
import groq
from groq import Groq


# ----------------------------------------------------------------------
# Small helpers for clear, stage-based logging -- deliberately defined
# BEFORE the Configuration section below, so that config-validation
# failures (the raise SystemExit(...) calls in that section) can log
# through the same log()/flush_summary() machinery as every other
# failure, instead of only ever appearing as a bare, timestamp-less
# traceback line in the raw Actions log with nothing written to the Job
# Summary tab. Added 2026-09-07 after noticing this gap during a logging
# audit -- previously log()/flush_summary() were defined much later in
# the file (after all config parsing), so a bad env var or a malformed
# control/pacing_<account>.json produced no Job Summary output at all.
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


def _fail_config(stage: str, detail: str):
    """
    Config-validation failure helper: logs through the normal log() +
    flush_summary() path (so it shows up in the Job Summary tab exactly
    like any runtime StageFailure does, with a timestamp and a clear
    header), then exits with the same SystemExit behavior the direct
    `raise SystemExit(...)` calls below already had -- argv/exit code
    and Actions' own failure marking are unchanged, this only adds
    logging on the way out. Zero cost on the success path: this only
    executes when a run is already about to fail at startup.
    """
    log(stage, "FAIL", detail)
    flush_summary(f"FAILED at startup: {stage} — {detail}")
    raise SystemExit(f"{stage}: {detail}")


# ----------------------------------------------------------------------
# Configuration (all from environment variables / GitHub Secrets)
# ----------------------------------------------------------------------

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_STRING = os.environ["TG_SESSION"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Three separate Groq accounts (separate orgs -> separate ITPM quotas),
# used round-robin across the quiz's questions so no single account's
# rate limit is hit by 5 rapid-fire calls each carrying the full
# SECTION_NOTES block (~2000+ input tokens/call). Replaces the old
# single GROQ_API_KEY -- only required if AI_PROVIDER=groq.
GROQ_API_KEYS = [
    (name, key)
    for name, key in (
        ("GROQ_API_KEY_1", os.environ.get("GROQ_API_KEY_1")),
        ("GROQ_API_KEY_2", os.environ.get("GROQ_API_KEY_2")),
        ("GROQ_API_KEY_3", os.environ.get("GROQ_API_KEY_3")),
    )
    if key
]

# Which AI answers the quiz questions. "groq" is the fast path (Groq's LPU
# hardware gives far more consistent low latency than Gemini has shown in
# testing); "gemini" is kept available as a fallback / for comparison.
AI_PROVIDER = os.environ.get("AI_PROVIDER", "groq").lower()
if AI_PROVIDER not in ("groq", "gemini"):
    _fail_config("Startup config", f"AI_PROVIDER must be 'groq' or 'gemini', got {AI_PROVIDER!r}")
if AI_PROVIDER == "groq" and not GROQ_API_KEYS:
    _fail_config("Startup config", "AI_PROVIDER=groq requires at least one of GROQ_API_KEY_1/2/3 to be set.")

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

# Positive-signal regex for recognizing an actual question message (used by
# _first_question_handler in main()). Matches the confirmed real format,
# e.g. "Question 1/5 \u23f1\ufe0f ...", "Question 2/5 ...". Configurable in
# case the bot's wording ever changes; case-insensitive.
QUESTION_TEXT_PATTERN = os.environ.get("QUESTION_TEXT_PATTERN", r"question\s+\d+\s*/\s*\d+")

def _parse_click_mode(env_name: str, default: str = "await") -> str:
    """Shared validation for START_QUIZ_CLICK_MODE / ANSWER_CLICK_MODE."""
    value = os.environ.get(env_name, default).strip().lower()
    if value not in ("await", "fire_and_forget", "skip"):
        print(f"::error::{env_name} must be 'await', 'fire_and_forget', or 'skip' (got '{value}')", file=sys.stderr)
        sys.exit(1)
    return value


# Controls how the "Start Quiz" button click is handled. This exists
# because message.click() on a callback button awaits Telegram's
# GetBotCallbackAnswerRequest, which blocks until the BOT answers -- or
# until Telegram's OWN server-side grace period elapses and it gives up,
# raising BotResponseTimeoutError (which Telethon silently swallows,
# returning None). CONFIRMED 2026-09-05 via a live controlled test: clicking
# an old Start Quiz button while the bot process was entirely offline still
# produced the exact same ~15s wait as a real run -- there was nothing on
# the other end to respond, so this can only be Telegram's own timeout
# ceiling, not real bot processing time. Most likely explanation: once Q1
# has already been sent, the bot doesn't answer that callback at all (same
# as it doesn't for a stale/expired Start Quiz button), so every real
# Start-Quiz click may just be waiting out that fixed ~15s ceiling for
# nothing -- confirmed harmless to skip, since Q1 arrives independent of
# this click either way (see _first_question_handler in main()).
#   "await"           (default/current behavior) -- click and wait for
#                      Telegram's confirmation before proceeding. Safest;
#                      guaranteed not to change behavior if the click turns
#                      out to matter after all.
#   "fire_and_forget"  -- send the click but don't wait for the bot's
#                      response; proceed to waiting for Q1 immediately.
#   "skip"             -- don't click at all.
START_QUIZ_CLICK_MODE = _parse_click_mode("START_QUIZ_CLICK_MODE")

# Same idea, applied to the 5 per-answer clicks instead. Different risk
# profile from Start Quiz: the thing that actually matters for correctness
# here isn't the click's own Telegram-level acknowledgment (confirmed:
# irrelevant -- if the bot sends the NEXT question, the answer registered,
# full stop, regardless of whether GetBotCallbackAnswerRequest ever
# resolved) -- it's whether the bot's answer-processing/quiz-advancing
# logic depends on that acknowledgment completing server-side, which is
# untested and may differ from Start Quiz's (confirmed) behavior. "await"
# stays the default for that reason; fire_and_forget/skip exist for live
# A/B testing, not as a safe assumption carried over from the Start Quiz
# finding.
ANSWER_CLICK_MODE = _parse_click_mode("ANSWER_CLICK_MODE")

TOTAL_QUESTIONS = int(os.environ.get("TOTAL_QUESTIONS", "5"))

# ---- Stage 1 timing ----
# The bot rejects /start before it opens, replying with the exact text in
# CHALLENGE_NOT_ACTIVE_TEXT. We don't message the bot at all until the open
# time, and we refuse to run entirely if started too early -- this script
# isn't meant to sit idle for a long time waiting.
#
# Real mode and test mode both follow this exact same shape, just anchored
# to a different open time:
#   - real mode  -> control/challenge_open_time.json  (default 8:00:01 PM
#                    EAT, i.e. 17:00:01 UTC -- NOT a plain 8:00 PM/17:00.
#                    Deliberately offset by 1 second -- see 2026-09-05
#                    notes: the confirmed ~15s Start-Quiz-click latency
#                    under real 17:00 UTC load might be worse for a
#                    request landing at the EXACT instant the challenge
#                    opens (thundering-herd-style burst) than one landing
#                    a beat later -- unconfirmed, but cheap to hedge
#                    against, and this makes the offset itself easy to A/B
#                    tune later without a code change. Lives in this repo
#                    file rather than a GitHub secret so it's editable
#                    directly from GitHub's web UI, entered in EAT
#                    12-hour format (e.g. "8:00:01 PM") -- converted to
#                    UTC internally at import time; every deadline/retry
#                    computation below still runs in UTC, only the config
#                    entry point and the "too early" message are EAT-
#                    facing. Replaces the old CHALLENGE_OPEN_TIME_UTC
#                    secret (2026-09-07 -- removed as redundant once this
#                    file existed).
#   - test mode  -> TEST_ACTIVATION_TIME_UTC        (whatever you set when
#                    you start the test bot, e.g. "13:00" -- test_bot.py
#                    enforces the exact same gate on its side, so test mode
#                    exercises the identical early-run-refusal / wait /
#                    retry-until-active behavior as a real run, just on a
#                    time of your choosing instead of a fixed 17:00)
#
# EARLIEST_RUN_MINUTES_BEFORE_OPEN / RETRY_WINDOW_MINUTES_AFTER_OPEN are
# shared by both modes -- 15 minutes early is too early to bother waiting
# for, and 5 minutes of retrying after open time is enough to absorb the
# bot being a beat late to actually activate.
CHALLENGE_NOT_ACTIVE_TEXT = "This challenge is not active yet."
# The bot's reply once a challenge has ended, e.g. "This challenge ended at
# 2:10 PM." -- unlike CHALLENGE_NOT_ACTIVE_TEXT, retrying can NEVER help
# here (the challenge is over, not merely "not yet"), so this must fail
# the run immediately on first sighting rather than retry it like the
# not-yet-active case. Matched as a substring since the exact closing
# time varies. Added 2026-09-06 after a real run kept retrying against a
# closed challenge every retry_interval_seconds until Telegram's own
# per-account send-rate limit kicked in (FloodWaitError, "A wait of 3335
# seconds is required" -- an unhandled crash, not a clean stop).
CHALLENGE_CLOSED_TEXT = "This challenge ended at"

# EAT (East Africa Time) is UTC+3 year-round -- no daylight saving, so this
# is a safe fixed offset rather than something that needs a timezone
# database lookup.
EAT_UTC_OFFSET_HOURS = 3

EAT_TIME_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "control", "challenge_open_time.json")


def _parse_12h_eat_time(text: str) -> tuple[int, int, int]:
    """
    Parses a 12-hour EAT time string with required seconds, e.g.
    "8:00:01 PM" or "8:00:01 AM", into 24-hour (hour, minute, second).
    Seconds are required (not optional) per this project's explicit need
    for sub-minute precision here (see EAT_UTC_OFFSET_HOURS comment above
    for why 17:00:01 and not a flat 17:00 matters). Raises ValueError with
    a clear message on anything else -- caught by the caller and turned
    into a _fail_config() call, not left to surface as a raw traceback.
    """
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2}):(\d{2})\s*([AaPp][Mm])\s*", text)
    if not m:
        raise ValueError(f'expected 12-hour time with seconds, e.g. "8:00:01 PM", got {text!r}')
    hour, minute, second, ampm = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4).upper()
    if not (1 <= hour <= 12):
        raise ValueError(f"hour must be 1-12 for 12-hour format, got {hour}")
    if not (0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError(f"minute/second out of range in {text!r}")
    hour_24 = hour % 12  # 12 AM -> 0, 12 PM -> 12, else unchanged
    if ampm == "PM":
        hour_24 += 12
    return hour_24, minute, second


def _load_challenge_open_time_utc() -> tuple[str, str]:
    """
    Reads control/challenge_open_time.json (open_time_eat, 12-hour EAT
    with required seconds) and returns (utc_str, eat_str):
      - utc_str is the equivalent UTC time as "HH:MM:SS", ready for
        today_utc_at() -- the same string shape CHALLENGE_OPEN_TIME_UTC
        used to be, so nothing downstream needs to change to stay
        EAT-aware.
      - eat_str is the original EAT string as given in the config file
        (e.g. "8:00:01 PM"), kept around purely so main()'s "too early"
        and waiting messages can show EAT -- the time the person
        actually recognizes -- alongside/instead of UTC. Every
        deadline/retry computation still runs on utc_str internally;
        eat_str is display-only.

    Like pacing config, this is required and fails loudly (_fail_config)
    on anything missing/malformed rather than silently falling back --
    the real challenge start time is exactly the kind of thing that must
    not silently default to something wrong.
    """
    try:
        with open(EAT_TIME_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        _fail_config(
            "Startup config",
            f"control/challenge_open_time.json not found at {EAT_TIME_CONFIG_PATH} -- required for real-mode timing.",
        )
    except json.JSONDecodeError as e:
        _fail_config("Startup config", f"control/challenge_open_time.json is not valid JSON: {e}")

    if not isinstance(raw, dict) or "open_time_eat" not in raw:
        _fail_config(
            "Startup config",
            'control/challenge_open_time.json must be a JSON object with an "open_time_eat" key, '
            'e.g. {"open_time_eat": "8:00:01 PM"}.',
        )

    eat_str = raw["open_time_eat"]
    try:
        eat_hour, eat_minute, eat_second = _parse_12h_eat_time(eat_str)
    except ValueError as e:
        _fail_config("Startup config", f"control/challenge_open_time.json: open_time_eat is invalid -- {e}")

    utc_hour = (eat_hour - EAT_UTC_OFFSET_HOURS) % 24
    utc_str = f"{utc_hour:02d}:{eat_minute:02d}:{eat_second:02d}"
    print(f"[challenge open time] Loaded {EAT_TIME_CONFIG_PATH}: {eat_str} EAT -> {utc_str} UTC")
    return utc_str, eat_str


if TEST_MODE:
    CHALLENGE_OPEN_TIME_UTC = None
    CHALLENGE_OPEN_TIME_EAT = None
else:
    CHALLENGE_OPEN_TIME_UTC, CHALLENGE_OPEN_TIME_EAT = _load_challenge_open_time_utc()
TEST_ACTIVATION_TIME_UTC = os.environ.get("TEST_ACTIVATION_TIME_UTC")                # HH:MM, UTC -- test mode
EARLIEST_RUN_MINUTES_BEFORE_OPEN = float(os.environ.get("EARLIEST_RUN_MINUTES_BEFORE_OPEN", "15"))
RETRY_WINDOW_MINUTES_AFTER_OPEN = float(os.environ.get("RETRY_WINDOW_MINUTES_AFTER_OPEN", "5"))
RETRY_INTERVAL_SECONDS = float(os.environ.get("RETRY_INTERVAL_SECONDS", "2"))

OPEN_TIME_UTC = TEST_ACTIVATION_TIME_UTC if TEST_MODE else CHALLENGE_OPEN_TIME_UTC
if TEST_MODE and not OPEN_TIME_UTC:
    _fail_config(
        "Startup config",
        "TEST_ACTIVATION_TIME_UTC is required in test mode -- set it to the same "
        "activation time (HH:MM, UTC) you gave the 'Run Test Bot' workflow.",
    )

# Once Start Quiz has been clicked, this is the separate time budget for the
# quiz-answering phase (Stage 3) -- independent of the Stage 1/2 gating above,
# since answering can legitimately run past the Stage 1/2 deadline once started.
QUIZ_TIMEOUT_MINUTES = float(os.environ.get("QUIZ_TIMEOUT_MINUTES", "15"))

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# qwen/qwen3.6-27b is the default Groq model (switched from openai/gpt-oss-20b
# on 2026-09-06) -- higher benchmarked intelligence (see decisions in
# context.json), with its known ITPM rate-limit exposure addressed by the
# 3-key round-robin (GROQ_API_KEY_1/2/3) rather than by picking a smaller
# model. gpt-oss-20b remains available via GROQ_MODEL override; it was
# Groq's recommended replacement for llama-3.1-8b-instant (deprecated
# August 2026) and is still the faster/cheaper option if that's ever
# preferred over accuracy again.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.6-27b")


def _resolve_groq_reasoning_effort(model_name: str, thinking_level: str):
    """Look up model_name in GROQ_REASONING_SCHEMES (substring match) and
    return the reasoning_effort value for the given THINKING_LEVEL. Falls
    back to the gpt-oss scheme for unrecognized models, with a log note --
    see GROQ_REASONING_SCHEMES above for why this is a lookup table rather
    than if/elif branching. Returns None for model families (e.g.
    groq/compound) that reject the reasoning_effort parameter outright --
    the call site must then omit it from the API kwargs entirely, since
    unlike qwen3.6 there is no valid string value that satisfies these
    models; None here specifically means "don't send this parameter"."""
    # NOTE: this runs at module-load time, before the log() helper is
    # defined further down the file -- use plain print() here, not log().
    model_lower = model_name.lower()
    for family_substring, scheme in GROQ_REASONING_SCHEMES.items():
        if family_substring in model_lower:
            return scheme[thinking_level]
    print(
        f"[Startup] ℹ️ GROQ_MODEL '{model_name}' isn't in GROQ_REASONING_SCHEMES -- "
        f"falling back to the '{_DEFAULT_GROQ_SCHEME_NAME}' reasoning_effort "
        "scheme. If this model rejects that value, add a new entry to "
        "GROQ_REASONING_SCHEMES for it.",
        flush=True,
    )
    return GROQ_REASONING_SCHEMES[_DEFAULT_GROQ_SCHEME_NAME][thinking_level]

# Unified thinking/reasoning-effort control for BOTH providers, so you can
# A/B test speed vs. correctness with one input regardless of which
# provider is active. Accepts "minimal", "low" (default, fastest),
# "medium", or "high".
#   - Gemini 3 (gemini-3.5-flash-lite) natively supports all four values
#     via thinking_level -- passed straight through, see ask_gemini_for_answer.
#   - Groq model families each expose a DIFFERENT reasoning_effort scheme
#     (confirmed against Groq's live API, 2026-09):
#       * gpt-oss-20b / gpt-oss-120b -> only "low"/"medium"/"high" (both
#         "none" and "minimal" get a 400, despite "none" appearing in some
#         SDK type hints).
#       * qwen3.6-27b -> only "none"/"default" (a 400 on anything else,
#         including "low"/"medium"/"high") -- it's a binary reasoning
#         on/off switch, not a graduated dial.
#     GROQ_REASONING_SCHEMES below is a per-model-family lookup (matched by
#     model-name substring) so adding a future Groq model with yet another
#     scheme is a one-entry addition here, not new branching logic. Each
#     entry maps all 4 THINKING_LEVEL tiers onto that family's own valid
#     values. Unrecognized models fall back to the gpt-oss scheme (today's
#     default behavior) and log a note -- see GROQ_REASONING_EFFORT below.
GROQ_REASONING_SCHEMES = {
    # substring matched against GROQ_MODEL, case-insensitive, checked in
    # order -- keep more-specific substrings above their broader relatives.
    "gpt-oss": {
        "minimal": "low", "low": "low", "medium": "medium", "high": "high",
    },
    "qwen3.6": {
        # No graduated levels exist on this model -- minimal AND low both
        # mean "don't bother reasoning", medium/high both mean "use the
        # model's own (fixed-depth) reasoning pass". Per user preference,
        # low maps to none (not default) since low signals "fast/shallow".
        "minimal": "none", "low": "none", "medium": "default", "high": "default",
    },
    "compound": {
        # groq/compound is an agentic system (tool calls/web search/code
        # execution under the hood), not a plain chat model -- its API
        # rejects reasoning_effort outright with a 400 ("reasoning_effort
        # is not supported with this model"), regardless of value. Unlike
        # qwen3.6 there's no valid string that satisfies it, so every tier
        # maps to None, which _resolve_groq_reasoning_effort and the
        # ask_groq_for_answer call site both treat as "omit the parameter
        # from the API call entirely" rather than "send this value".
        "minimal": None, "low": None, "medium": None, "high": None,
    },
}
_DEFAULT_GROQ_SCHEME_NAME = "gpt-oss"

THINKING_LEVEL = os.environ.get("THINKING_LEVEL", "low").strip().lower()
_VALID_THINKING_LEVELS = ("minimal", "low", "medium", "high")
if THINKING_LEVEL not in _VALID_THINKING_LEVELS:
    _fail_config("Startup config", f"THINKING_LEVEL must be one of {_VALID_THINKING_LEVELS}, got {THINKING_LEVEL!r}")

# Groq-specific value derived from THINKING_LEVEL + GROQ_MODEL -- see
# GROQ_REASONING_SCHEMES and _resolve_groq_reasoning_effort above. Can be
# None (e.g. for groq/compound) meaning "omit reasoning_effort entirely" --
# ask_groq_for_answer's _call_groq() builds its kwargs dict conditionally
# to handle that, rather than always passing this value straight through.
GROQ_REASONING_EFFORT = _resolve_groq_reasoning_effort(GROQ_MODEL, THINKING_LEVEL)

# Optional extra sentence appended to every prompt (both Groq and Gemini),
# meant to nudge the model to read qualifying words/phrasing more carefully
# before answering -- e.g. "in modern market", "NOT true", "FALSE" -- the
# kind of wording that's easy to skim past. Empty by default (no change to
# the prompt at all). Set PROMPT_EXTRA_INSTRUCTION to test a nudge; edit
# its wording here without touching the rest of the prompt-building logic.
PROMPT_EXTRA_INSTRUCTION = os.environ.get("PROMPT_EXTRA_INSTRUCTION", "")

# Optional section reference notes -- extracted/summarized info from a
# section's video, attached in full to EVERY question's prompt for this
# run (not per-question matching -- simpler, and the token cost is small
# relative to Groq's speed). Lets the AI answer "in the Section Video..."
# style questions, and others where a fact (not just more reasoning) was
# the actual gap -- see 2026-09-04 session notes in context.json.
#
# HOW TO ADD/UPDATE NOTES (no code editing needed): drop a Markdown file
# into the section_notes/ folder via GitHub's web UI (Add file -> Create
# new file). Name it after the section, e.g. section_notes/section_5.md.
# Then set SECTION_NOTES to that filename without the .md extension (e.g.
# "section_5") via the workflow's section_notes input / this env var.
# Leave blank/unset for no notes attached (old behavior, unchanged).
SECTION_NOTES_NAME = os.environ.get("SECTION_NOTES", "").strip()
SECTION_NOTES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "section_notes")


def _load_section_notes() -> str:
    """
    Reads section_notes/<SECTION_NOTES_NAME>.md if set, else returns "".
    Missing file prints a plain warning and continues with no notes (never
    fails the run over this -- notes are a prompt enhancement, not a
    requirement). Uses plain print/logging here, not the log() stage
    helper below, since this runs at import time before log() exists and
    before the GitHub summary machinery is set up.
    """
    if not SECTION_NOTES_NAME:
        return ""
    path = os.path.join(SECTION_NOTES_DIR, f"{SECTION_NOTES_NAME}.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            notes = f.read().strip()
        print(f"[section notes] Loaded {path} ({len(notes)} chars)")
        return notes
    except FileNotFoundError:
        print(f"[section notes] WARNING: SECTION_NOTES={SECTION_NOTES_NAME!r} but {path} was not found -- continuing with no notes")
        return ""


SECTION_NOTES_TEXT = _load_section_notes()


# Per-question pacing floor: each question's answer click is held back
# until at least N seconds have passed since that question's message was
# received, so the run doesn't look like every question got answered
# near-instantly (a distinctive, bot-like pattern) with pacing only
# applied to the final click, as before. Replaces the old
# MIN_SECONDS_SINCE_START_QUIZ (single floor gating only Question 5's
# click, anchored to /start challenge_N being sent).
#
# Also holds max_quiz_seconds: a ceiling on total time since the Start
# Quiz button was clicked (not since the bot responded to that click --
# in fire_and_forget mode there may be no prompt response to anchor to,
# and even in "await" mode the response time is itself variable, so the
# click is the only stable, deterministic anchor). Each question's
# pacing wait is capped so it never pushes total elapsed past this
# ceiling -- e.g. if 28s have passed and a question's own floor would
# reach 33s, it only waits 2s (to reach 30s), not the full floor.
#
# Lives in control/pacing_<PACING_CONFIG_NAME>.json (not a single shared
# file) so SAF and ETH -- the two Telegram testing accounts selectable via
# the workflow's "account" dropdown -- can be paced differently, e.g. if
# one account's timing pattern needs to look distinct from the other's.
# PACING_CONFIG_NAME defaults to "SAF" (matching the workflow's own
# account-dropdown default) and is resolved automatically from the SAME
# "account" choice at the workflow level -- there's no separate pacing
# file selector to set; choosing SAF/ETH as the account implies its
# matching pacing file. Editable straight from GitHub's web UI without
# touching the workflow or any secrets. Shape:
#   {"default": 3, "1": 2, "2": 2, "3": 2.5, "4": 3, "5": 3, "max_quiz_seconds": 30}
# "default" and "max_quiz_seconds" are both required. "default" is the
# floor for any question number not given its own key. A minimal
# {"default": 3, "max_quiz_seconds": 30} with no per-question keys is
# valid too -- every question then uses the same 3s floor, capped at 30s
# total.
PACING_CONFIG_NAME = os.environ.get("PACING_CONFIG_NAME", "SAF")
PACING_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "control", f"pacing_{PACING_CONFIG_NAME}.json"
)


def _load_pacing_config() -> tuple[dict, float]:
    """
    Reads control/pacing_<PACING_CONFIG_NAME>.json and returns
    (per_question, max_quiz_seconds):
      - per_question: {question_number: seconds} resolved for every
        question in 1..TOTAL_QUESTIONS (falling back to "default" for any
        question not explicitly listed).
      - max_quiz_seconds: the total-elapsed-since-Start-Quiz-click ceiling
        that per-question waits get capped against.

    Unlike section notes, pacing is a deliberate anti-detection measure,
    not an optional enhancement -- a missing or malformed file, or a
    missing required key, fails the run loudly (SystemExit) rather than
    silently pacing at 0s, which would defeat the purpose without anyone
    noticing.
    """
    try:
        with open(PACING_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        _fail_config(
            "Startup config",
            f"control/pacing_{PACING_CONFIG_NAME}.json not found at {PACING_CONFIG_PATH} "
            "-- required for per-question pacing.",
        )
    except json.JSONDecodeError as e:
        _fail_config("Startup config", f"control/pacing_{PACING_CONFIG_NAME}.json is not valid JSON: {e}")

    if not isinstance(raw, dict) or "default" not in raw or "max_quiz_seconds" not in raw:
        _fail_config(
            "Startup config",
            f'control/pacing_{PACING_CONFIG_NAME}.json must be a JSON object with "default" and '
            '"max_quiz_seconds" keys, e.g. {"default": 3, "max_quiz_seconds": 30}.',
        )

    def _as_seconds(value, key):
        try:
            return float(value)
        except (TypeError, ValueError):
            _fail_config("Startup config", f"control/pacing_{PACING_CONFIG_NAME}.json: value for {key!r} must be a number, got {value!r}.")

    default_seconds = _as_seconds(raw["default"], "default")
    max_quiz_seconds = _as_seconds(raw["max_quiz_seconds"], "max_quiz_seconds")
    per_question = {}
    for q in range(1, TOTAL_QUESTIONS + 1):
        key = str(q)
        per_question[q] = _as_seconds(raw[key], key) if key in raw else default_seconds

    print(f"[pacing] Loaded {PACING_CONFIG_PATH}: per_question={per_question}, max_quiz_seconds={max_quiz_seconds}")
    return per_question, max_quiz_seconds


PACING_SECONDS_BY_QUESTION, MAX_QUIZ_SECONDS = _load_pacing_config()

# Temporary diagnostic switch: when true, logs every incoming message in
# the challenge bot's chat (scoped via chats=challenge_bot -- see the
# handler registration in main() for why it's scoped rather than global).
# Turn off once things are working reliably.
DEBUG_LOG_ALL_EVENTS = os.environ.get("DEBUG_LOG_ALL_EVENTS", "false").lower() == "true"


class _TelethonFloodWaitLogHandler(logging.Handler):
    """
    Telethon auto-sleeps through FloodWaitError/SlowModeWaitError as long as
    the wait is under its flood_sleep_threshold (60s by default) -- it does
    this INSIDE the request call (e.g. send_message), silently, with no
    exception raised. Without this handler, a flood-wait shows up as nothing
    more than an unexplained multi-second gap between two log lines, which
    is confusing to diagnose (e.g. "why did attempt 11 take 40s longer than
    attempt 10?" -- it didn't; Telethon was sleeping through a flood wait
    inside that call).

    Telethon logs these internally at INFO level with the message template
    "Sleeping%s for %ds (%s) on %s flood wait" (see
    telethon/client/users.py's _fmt_flood/_call) -- this handler catches
    just that record and re-emits it through our own log(), so it appears
    inline in the same timestamped stage log instead of being silently
    absorbed.
    """
    def emit(self, record):
        message = record.getMessage()
        if "flood wait" in message.lower():
            log("Telegram flood wait", "INFO", f"{message} -- this explains any gap before the next stage line")


def _install_telethon_flood_wait_logging():
    """
    Telethon's flood-wait sleep is logged via loggers named after the
    originating module (e.g. "telethon.client.users") at INFO level.
    Attaching to the "telethon" logger catches all of them regardless of
    which submodule the request came from, without needing every Telethon
    submodule name kept in sync with the library's internals.
    """
    telethon_logger = logging.getLogger("telethon")
    telethon_logger.setLevel(logging.INFO)
    telethon_logger.addHandler(_TelethonFloodWaitLogHandler())


class StageFailure(Exception):
    """Raised to stop the script with a clear stage name + reason."""
    def __init__(self, stage, detail):
        self.stage = stage
        self.detail = detail
        super().__init__(f"{stage}: {detail}")


def today_utc_at(hh_mm_or_hhmmss: str) -> datetime:
    """
    Parses 'HH:MM' or 'HH:MM:SS' into a UTC datetime for the current UTC
    calendar day. Seconds default to 0 if omitted, for backward
    compatibility with existing HH:MM values (e.g. TEST_ACTIVATION_TIME_UTC
    inputs, which stay HH:MM-only). CHALLENGE_OPEN_TIME_UTC (now derived
    from control/challenge_open_time.json's EAT value, see
    _load_challenge_open_time_utc) is the one real-mode value that
    actually uses seconds precision -- see its own definition/comment for
    why (2026-09-05: real vs. test 15s Start-Quiz-click-latency
    investigation).

    Pre-existing limitation, unchanged by the 2026-09-07 EAT config
    addition: this always anchors to TODAY's UTC calendar date, not the
    date implied by the EAT time. This is harmless for the expected
    ~8 PM EAT range (mid-afternoon-into-evening UTC, nowhere near the UTC
    day boundary), but an open_time_eat value between roughly midnight
    and 3 AM EAT would convert to a UTC time on the PREVIOUS UTC calendar
    day (e.g. 1:30:15 AM EAT -> 22:30:15 UTC) while still being anchored
    to today's UTC date here -- worth revisiting if the challenge's
    schedule ever moves into that window.
    """
    parts = [int(p) for p in hh_mm_or_hhmmss.split(":")]
    hour, minute = parts[0], parts[1]
    second = parts[2] if len(parts) > 2 else 0
    now = datetime.now(timezone.utc)
    return now.replace(hour=hour, minute=minute, second=second, microsecond=0)


# ----------------------------------------------------------------------
# Gemini / Groq: ask which option letter is correct, with strict output + retry
# ----------------------------------------------------------------------

_gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# One Groq client per configured key, built once at import time (a Groq
# client is just a thin HTTP wrapper around an API key -- no connection
# or handshake happens here, so holding several is free). Indexed
# round-robin by question number in ask_groq_for_answer.
_groq_clients = [(name, Groq(api_key=key)) for name, key in GROQ_API_KEYS]

_LETTERS = ["A", "B", "C", "D", "E", "F"]  # supports up to 6 options, just in case


def _options_are_bare_letters(options: list[str]) -> bool:
    """
    Detects the challenge bot's two confirmed real button styles (see
    inspect_bot_buttons.py output from 2026-09-04): Q2-Q4 put full option
    text on each button ("A) Stop", "B) Limit", ...), while Q1/Q5 use bare
    single-letter buttons ("A", "B", "C", "D") with the actual option text
    written into the question message itself instead. extract_mcq_options()
    can't distinguish these -- it just returns button.text either way -- so
    _build_prompt() needs to know which style it got to avoid asking the AI
    to choose between meaningless single-letter "options" ("A) A", "B) B",
    ...) when the button text carries no real information.

    Matching against the EXPECTED letter for each position (not just "is
    this option a single character") is deliberate: an option whose real
    text genuinely happens to be a single letter (unlikely here, but not
    impossible in general) won't be misdetected unless it also happens to
    be the correct positional letter for every option in the message --
    that combination is not realistically going to occur by coincidence.
    """
    if not options:
        return False
    return all(opt.strip().upper() == _LETTERS[i] for i, opt in enumerate(options) if i < len(_LETTERS))


def _strip_redundant_letter_prefix(option_text: str, letter: str) -> str:
    """
    Strips a leading "<letter>) " prefix from option_text if it's already
    there (case-insensitive) -- some sources (test_bot.py's non-bare-letter
    style, possibly some real question types) put the letter on the button
    text itself. Used wherever we're about to prepend our own letter, so
    the result never doubles up as "A) A) Stop".
    """
    stripped = option_text.strip()
    prefix = f"{letter})"
    if stripped.upper().startswith(prefix.upper()):
        return stripped[len(prefix):].strip()
    return stripped


def _build_prompt(question_text: str, options: list[str]) -> str:
    extra = f" {PROMPT_EXTRA_INSTRUCTION}" if PROMPT_EXTRA_INSTRUCTION else ""
    # Section reference notes (if SECTION_NOTES is set) go in full ahead of
    # the question -- same block reused for every question this run, not
    # matched per-question. See SECTION_NOTES_TEXT / _load_section_notes above.
    notes_block = f"Reference notes for this section:\n{SECTION_NOTES_TEXT}\n\n" if SECTION_NOTES_TEXT else ""

    if _options_are_bare_letters(options):
        # Don't emit a fabricated "Options:" block (it would literally read
        # "A) A", "B) B", ... — actively misleading, not just uninformative)
        # -- tell the model plainly that the lettered options are already
        # written into the question text above, matching what's really true
        # for this bot's Q1/Q5 style.
        options_block = (
            f"The {len(options)} answer choices ({', '.join(_LETTERS[:len(options)])}) "
            "are written directly in the question text above (e.g. \"A) ...\", \"B) ...\"). "
            "Pick the correct one and respond with its letter."
        )
    else:
        # Buttons here carry real option text, but some sources (e.g.
        # test_bot.py's non-bare_letters style, and possibly some real
        # question types) already prefix it with "A) ", "B) ", etc. -- if
        # we blindly prepend another letter on top, the result is a
        # confusing double-lettered line like "A) A) Stop". Strip a
        # pre-existing "<matching letter>) " prefix (case-insensitive)
        # before re-adding it, so the prompt shows each option's letter
        # exactly once regardless of which style this particular message
        # used.
        cleaned = []
        for i, opt in enumerate(options):
            letter = _LETTERS[i] if i < len(_LETTERS) else ""
            cleaned.append(_strip_redundant_letter_prefix(opt, letter) if letter else opt.strip())
        lettered = "\n".join(f"{_LETTERS[i]}) {opt}" for i, opt in enumerate(cleaned))
        options_block = f"Options:\n{lettered}"

    return (
        f"{notes_block}"
        "You are answering a multiple-choice question. "
        "Respond with ONLY the single letter of the correct option. "
        f"No words, no punctuation, no explanation — just the letter.{extra}\n\n"
        f"Question: {question_text}\n\n"
        f"{options_block}\n\n"
        "Answer (single letter only):"
    )


def _log_gemini_usage(resp, attempt_label: str) -> None:
    """
    Gemini equivalent of _log_groq_usage -- logs prompt/thoughts/candidates/
    total token counts from usage_metadata when present. Purely
    observational, same reasoning as the Groq version: helps size
    max_output_tokens correctly per model/thinking_level combo instead of
    guessing (see the MAX_TOKENS/empty-response issue this was added
    alongside, 2026-09-05).
    """
    usage = getattr(resp, "usage_metadata", None)
    if usage is None:
        return
    prompt_toks = getattr(usage, "prompt_token_count", None)
    thoughts_toks = getattr(usage, "thoughts_token_count", None)
    candidates_toks = getattr(usage, "candidates_token_count", None)
    total_toks = getattr(usage, "total_token_count", None)
    parts = [f"prompt={prompt_toks}", f"thoughts={thoughts_toks}", f"candidates={candidates_toks}", f"total={total_toks}"]
    log(f"Gemini answer ({attempt_label})", "INFO", f"token usage -- {', '.join(parts)}")


def _gemini_finish_reason(resp) -> str:
    """Best-effort extraction of finish_reason for diagnostics when text is empty."""
    try:
        candidates = getattr(resp, "candidates", None) or []
        if candidates:
            reason = getattr(candidates[0], "finish_reason", None)
            return str(reason) if reason is not None else "unknown"
    except Exception:
        pass
    return "unknown"


def ask_gemini_for_answer(question_text: str, options: list[str], attempt_label: str) -> str:
    """
    Returns the chosen option letter (e.g. "B"). Raises StageFailure if the
    model output can't be parsed into a valid option even after retry.
    """
    valid_letters = _LETTERS[: len(options)]

    # thinking_level is tunable via THINKING_LEVEL (default "minimal" was
    # previously hardcoded here; now shared with Groq's reasoning_effort --
    # see THINKING_LEVEL above). NOTE: not every Gemini model supports every
    # level -- e.g. gemini-3.7-flash rejects "minimal" outright (400 error),
    # while gemini-3.5-flash-lite defaults to "minimal" and accepts it fine.
    # We don't remap here (unlike Groq) since which levels a given
    # GEMINI_MODEL supports can change per model; if you pick an
    # unsupported level for your chosen model, the API will error clearly.
    #
    # max_output_tokens: thinking-capable Gemini models spend SOME tokens on
    # internal reasoning even at "low"/"minimal", and those tokens count
    # against max_output_tokens. Too small a budget (previously 8) causes
    # MAX_TOKENS finish_reason with response.text/.parsed BOTH silently
    # None -- not an exception, just an empty result -- confirmed 2026-09-05
    # with gemini-3.7-flash at thinking_level=low (worked fine on
    # gemini-3.5-flash-lite's default "minimal", which apparently uses
    # ~0 thinking tokens). Same root cause as the earlier Groq
    # json_validate_failed fix (20 -> 300); mirroring that headroom here.
    generation_config = genai_types.GenerateContentConfig(
        temperature=0,
        response_mime_type="text/x.enum",
        response_schema={"type": "STRING", "enum": valid_letters},
        thinking_config=genai_types.ThinkingConfig(thinking_level=THINKING_LEVEL),
        max_output_tokens=300,
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
        _log_gemini_usage(resp, attempt_label)
        text = (resp.text or "").strip().upper()
        if not text:
            reason = _gemini_finish_reason(resp)
            log(f"Gemini answer ({attempt_label})", "INFO", f"empty response text (finish_reason={reason}) -- likely hit max_output_tokens on thinking tokens")
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


def _describe_groq_error(e: Exception) -> str:
    """
    Extracts the useful detail from a Groq SDK exception for logging --
    specifically for RateLimitError (429/ITPM), where the parsed response
    body already contains the full human-readable detail (limit/used/
    requested numbers, retry hint, e.g. "Rate limit reached for model
    `qwen/qwen3.6-27b` ... Limit 7000, Used 6032, Requested 2115. Please
    try again in 9.8s") -- confirmed against the installed groq==1.7.0
    source: APIStatusError.body is json.loads() of the response text when
    it's valid JSON, same shape Groq's own dashboard shows. Prefers
    body["message"] (the clean sentence) over e.message (which wraps it as
    "Error code: 429 - {the whole dict}") when available. Falls back to
    str(e) for any exception type that doesn't carry this structure
    (network errors, timeouts, etc.), so this is always safe to call.
    Added 2026-09-07 after having to manually check groq.com's own
    console to get this same detail for a real ITPM incident -- now it's
    in the run's own log instead.
    """
    if isinstance(e, groq.APIStatusError):
        if isinstance(e.body, dict) and "message" in e.body:
            code = e.body.get("code")
            return e.body["message"] + (f" [code={code}]" if code else "")
        return e.message
    return str(e)


def _log_groq_usage(resp, attempt_label: str) -> None:
    """
    Logs actual token usage from a Groq response -- prompt/completion/total,
    plus a reasoning-token breakdown when Groq's API actually returns one
    (as of this writing that field is inconsistently populated for
    reasoning models like gpt-oss-20b, sometimes 0 even when real reasoning
    happened -- see https://community.groq.com/t/gpt-oss-120b-reasoning-tokens-not-counted-in-responses-api-usage-statistics/555,
    so this only reports it when present rather than assuming it's
    accurate). This is purely observational -- doesn't affect answering
    logic -- added to see real-world token spend per question, e.g. when
    sizing max_completion_tokens or estimating the cost of adding extra
    context (like a video transcript) to the prompt.
    """
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    prompt_toks = getattr(usage, "prompt_tokens", None)
    completion_toks = getattr(usage, "completion_tokens", None)
    total_toks = getattr(usage, "total_tokens", None)
    reasoning_toks = None
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        reasoning_toks = getattr(details, "reasoning_tokens", None)

    parts = [f"prompt={prompt_toks}", f"completion={completion_toks}", f"total={total_toks}"]
    if reasoning_toks is not None:
        parts.append(f"reasoning={reasoning_toks}")
    log(f"Groq answer ({attempt_label})", "INFO", f"token usage -- {', '.join(parts)}")


def ask_groq_for_answer(question_text: str, options: list[str], attempt_label: str, q_num: int) -> str:
    """
    Groq equivalent of ask_gemini_for_answer(). Same shape, same return
    value (a single option letter), so the call site doesn't need to know
    which provider is in use.

    q_num (1-based question number) picks which of the configured Groq
    accounts handles this call, round-robin (q_num - 1) % len(_groq_clients)
    -- spreads the ~2000+ input tokens/call (SECTION_NOTES attached in
    full each time) across separate accounts/ITPM quotas so consecutive
    questions never stack against the same account's rate limit. Logging
    which key answered is just a print() (see log()) -- no extra network
    call, so this adds no latency.
    """
    key_name, groq_client = _groq_clients[(q_num - 1) % len(_groq_clients)]
    log(f"Groq answer ({attempt_label})", "INFO", f"using {key_name}")

    valid_letters = _LETTERS[: len(options)]
    prompt = _build_prompt(question_text, options)

    # JSON Schema mode with strict=True forces the model to return exactly
    # {"answer": "<one of the valid letters>"} -- no free text, no
    # explanation, nothing to parse out with a regex. reasoning_effort is
    # tunable via the shared THINKING_LEVEL env var (see above; "minimal"
    # maps to "low" for Groq, since Groq's live API rejects both "none" and
    # "minimal" with a 400 despite "none" appearing in some SDK type hints).
    # Important: gpt-oss-20b ALWAYS spends some tokens reasoning before the
    # JSON answer, even at "low" -- those reasoning tokens count against
    # max_completion_tokens. A too-low budget (e.g. 20) gets cut off mid-
    # reasoning before any JSON is written, causing a strict-mode
    # json_validate_failed 400. Give it enough headroom for the reasoning
    # pass plus the short JSON answer; 300 is comfortably enough even at
    # "medium" effort while still being fast.
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "quiz_answer",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string", "enum": valid_letters},
                },
                "required": ["answer"],
                "additionalProperties": False,
            },
        },
    }

    def _call_groq():
        max_attempts = 3
        last_error = None
        # Some model families (groq/compound) reject reasoning_effort
        # outright, even set to "none"/"default" -- GROQ_REASONING_EFFORT
        # is None for those (see GROQ_REASONING_SCHEMES), and the parameter
        # must be left out of kwargs entirely rather than passed as None,
        # since the SDK would otherwise still send it.
        groq_kwargs = {
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_completion_tokens": 300,
            "response_format": response_format,
        }
        if GROQ_REASONING_EFFORT is not None:
            groq_kwargs["reasoning_effort"] = GROQ_REASONING_EFFORT
        for attempt in range(1, max_attempts + 1):
            try:
                return groq_client.chat.completions.create(**groq_kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_attempts:
                    log(
                        f"Groq answer ({attempt_label})",
                        "INFO",
                        f"API call failed ({e.__class__.__name__}: {_describe_groq_error(e)}), "
                        f"retrying (attempt {attempt}/{max_attempts})",
                    )
                    time.sleep(0.5)
        raise StageFailure(
            f"Groq answer ({attempt_label})",
            f"Groq API call failed after {max_attempts} attempts: "
            f"{last_error.__class__.__name__}: {_describe_groq_error(last_error)}",
        )

    def _try_once():
        resp = _call_groq()
        _log_groq_usage(resp, attempt_label)
        raw = (resp.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(raw)
            letter = str(parsed.get("answer", "")).strip().upper()
        except Exception:
            letter = ""
        if letter not in valid_letters:
            # Fall back to scanning for a bare letter, in case strict mode
            # wasn't honored for some reason.
            match = re.search(r"[A-F]", raw.upper())
            letter = match.group(0) if match else None
        return letter

    letter = _try_once()
    if letter in valid_letters:
        log(f"Groq answer ({attempt_label})", "OK", f"chose {letter}")
        return letter

    log(f"Groq answer ({attempt_label})", "INFO", f"unparseable response '{letter}', retrying once")
    letter = _try_once()
    if letter in valid_letters:
        log(f"Groq answer ({attempt_label}, retry)", "OK", f"chose {letter}")
        return letter

    raise StageFailure(
        f"Groq answer ({attempt_label})",
        f"could not get a valid option letter after retry (last raw value: {letter!r})",
    )


def ask_ai_for_answer(question_text: str, options: list[str], attempt_label: str, q_num: int) -> str:
    """Dispatches to whichever provider AI_PROVIDER selects. q_num (1-based)
    is only used by Groq, to round-robin across the configured accounts --
    see ask_groq_for_answer."""
    if AI_PROVIDER == "groq":
        return ask_groq_for_answer(question_text, options, attempt_label, q_num)
    return ask_gemini_for_answer(question_text, options, attempt_label)


# ----------------------------------------------------------------------
# Telegram button helpers
# ----------------------------------------------------------------------

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

# Tracks background tasks spawned by click_button_or_follow_deep_link's
# fire_and_forget mode, so main() can give them a bounded chance to finish
# (and log their actual duration) before disconnecting -- rather than the
# client tearing down mid-request and silently discarding that diagnostic
# data. See the "Wait briefly for any fire_and_forget click" step in main().
_background_click_tasks: list = []


async def click_button_or_follow_deep_link(client, message, row, col, stage_name, click_mode="await"):
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
    click_mode only affects the callback-button case ("await" is always used
    unconditionally for URL buttons, regardless of what the caller passes --
    see START_QUIZ_CLICK_MODE / ANSWER_CLICK_MODE for how callers choose a
    mode for their respective click):
      - "await": click and wait for Telegram's confirmation as before.
      - "fire_and_forget": send the click, don't wait for the bot's
        response -- return immediately so the caller can move on to
        whatever's next (e.g. waiting for Q1) without sitting through the
        bot's slow callback-answer time.
      - "skip": don't click at all.
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

    if click_mode == "skip":
        log(stage_name, "OK", "click skipped (click_mode=skip)")
        return None

    # Not a URL button -> normal callback button, .click() is correct here.
    # Time the call itself: message.click() performs Telegram's
    # GetBotCallbackAnswer RPC, which Telegram forwards to the bot's own
    # backend and waits for a response before returning to us -- so a slow
    # click here reflects the BOT being slow/queued, not anything in this
    # script. Logging the duration turns "why was there a mystery gap
    # between these two log lines" into a number you can see directly
    # (confirmed real: ~15s gaps seen on two separate accounts on
    # 2026-09-04, with no flood-wait log line and no sleep() in this code
    # path -- so the time was spent inside Telegram/the bot, not here).
    click_started = time.monotonic()

    if click_mode == "fire_and_forget":
        # Schedule the click as a background task instead of awaiting it.
        # We still log how long it actually took once it eventually
        # completes, so fire_and_forget runs remain comparable to await
        # runs for A/B timing purposes -- we just don't block on it here.
        async def _click_and_log():
            try:
                await message.click(row, col)
                duration = time.monotonic() - click_started
                log(stage_name, "OK", f"clicked callback button, response received in background ({duration:.1f}s)")
            except Exception as e:
                log(stage_name, "INFO", f"background click raised {type(e).__name__}: {e} (ignored -- fire_and_forget)")

        task = asyncio.create_task(_click_and_log())
        _background_click_tasks.append(task)
        log(stage_name, "OK", "clicked callback button (fire_and_forget -- not waiting for response)")
        return None

    await message.click(row, col)
    click_duration = time.monotonic() - click_started
    log(stage_name, "OK", f"clicked callback button ({click_duration:.1f}s)")
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

    Returns (message, (row, col), sent_at) for the matched button, where
    sent_at is the time.monotonic() timestamp of the specific "/start" send
    that led to this response (i.e. not an earlier rejected attempt).
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
        if CHALLENGE_CLOSED_TEXT in text:
            log(stage_name, "INFO", f"bot reports the challenge has closed (preview: '{preview}') -- stopping, retrying can't help")
            result_fut.set_exception(StageFailure(stage_name, f"challenge is closed: '{preview}'"))
        elif CHALLENGE_NOT_ACTIVE_TEXT in text:
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

            sent_at = time.monotonic()
            await client.send_message(challenge_bot, start_command)
            log(stage_name, "INFO", f"sent '{start_command}' (attempt {attempt})")

            wait_for = min(remaining, retry_interval_seconds)
            try:
                msg, loc = await asyncio.wait_for(asyncio.shield(result_fut), timeout=wait_for)
                log(stage_name, "OK", f"found matching button at row {loc[0]}, col {loc[1]} (attempt {attempt})")
                return msg, loc, sent_at
            except asyncio.TimeoutError:
                attempt += 1
                continue
    finally:
        client.remove_event_handler(handler, events.NewMessage(chats=challenge_bot))


async def main():
    _install_telethon_flood_wait_logging()

    # Consolidated startup config summary -- everything that affects HOW
    # this run behaves, resolved and printed together in one place. Added
    # 2026-09-07 after noticing this info was previously only
    # reconstructable by cross-referencing several scattered sources (the
    # workflow's override-or-default echo lines, the section-notes/pacing
    # print lines, this script's own env var defaults) -- e.g. confirming
    # which Groq model a "no overrides" run actually resolved to required
    # reading the source. One log() call, no extra I/O or network access,
    # so this adds no latency -- purely printing values already resolved
    # above.
    log(
        "Startup config",
        "INFO",
        f"mode={'TEST MODE' if TEST_MODE else 'real mode'} | "
        f"AI_PROVIDER={AI_PROVIDER} | "
        + (
            f"GROQ_MODEL={GROQ_MODEL} reasoning_effort={GROQ_REASONING_EFFORT!r} "
            f"groq_accounts={len(_groq_clients)} ({', '.join(name for name, _ in _groq_clients)})"
            if AI_PROVIDER == "groq"
            else f"GEMINI_MODEL={GEMINI_MODEL}"
        )
        + f" | THINKING_LEVEL={THINKING_LEVEL} | "
        f"START_QUIZ_CLICK_MODE={START_QUIZ_CLICK_MODE} ANSWER_CLICK_MODE={ANSWER_CLICK_MODE} | "
        f"PACING_CONFIG_NAME={PACING_CONFIG_NAME} | "
        f"SECTION_NOTES={SECTION_NOTES_NAME or '(none)'} | "
        f"TOTAL_QUESTIONS={TOTAL_QUESTIONS}",
    )

    now = datetime.now(timezone.utc)

    open_time = today_utc_at(OPEN_TIME_UTC)
    earliest_run_time = open_time - timedelta(minutes=EARLIEST_RUN_MINUTES_BEFORE_OPEN)
    deadline = open_time + timedelta(minutes=RETRY_WINDOW_MINUTES_AFTER_OPEN)
    mode_label = "TEST MODE" if TEST_MODE else "real mode"

    # Real mode shows the activation time in EAT (what the person actually
    # recognizes -- control/challenge_open_time.json is entered in EAT),
    # with UTC alongside for anyone cross-checking logs/timestamps. Test
    # mode has no EAT value at all (TEST_ACTIVATION_TIME_UTC is UTC-only),
    # so it keeps the plain UTC-only wording it always had. Only the text
    # shown here changes -- open_time/earliest_run_time/deadline above are
    # still computed in UTC exactly as before.
    open_time_display = f"{CHALLENGE_OPEN_TIME_EAT} EAT ({OPEN_TIME_UTC} UTC)" if not TEST_MODE else f"{OPEN_TIME_UTC} UTC"

    if now < earliest_run_time:
        raise StageFailure(
            "Startup check",
            f"[{mode_label}] it's {now.strftime('%H:%M:%S')} UTC, which is more than "
            f"{EARLIEST_RUN_MINUTES_BEFORE_OPEN:.0f} minutes before the {open_time_display} activation "
            f"time. Trigger the workflow again closer to {open_time_display}.",
        )

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start()
    log("Telegram login", "OK", "session authenticated")

    try:
        challenge_bot = await client.get_entity(CHALLENGE_BOT_USERNAME)
        log("Resolve bot", "INFO", f"resolved '@{CHALLENGE_BOT_USERNAME}' -> id={challenge_bot.id} ({mode_label})")

        if DEBUG_LOG_ALL_EVENTS:
            # Scoped to the challenge bot's chat only (chats=challenge_bot),
            # matching every other handler in this file. This used to be a
            # bare events.NewMessage() with no chat filter -- that meant
            # every single incoming message on the whole account (any group,
            # any DM) ran this handler, including an await event.get_chat()
            # RPC call per message. On a fresh/uncached chat that's a real
            # network round-trip; a burst of unrelated group traffic could
            # back up Telethon's single-threaded update dispatch and delay
            # processing of other things on the same loop -- including the
            # response to our own button click. Scoping to the bot's chat
            # removes both the noise and the extra RPC (chat identity is
            # already known -- it's always challenge_bot -- so get_chat()
            # was redundant here regardless of the handler being global).
            async def _debug_any_event(event):
                msg = event.message
                has_buttons = bool(msg.buttons)
                preview = (msg.text or "").strip().replace("\n", " ")[:60]
                log("DEBUG any event", "INFO",
                    f"chat_id={challenge_bot.id} has_buttons={has_buttons} preview='{preview}'")
            client.add_event_handler(_debug_any_event, events.NewMessage(chats=challenge_bot))
            log("DEBUG mode", "INFO", "logging all incoming events from the challenge bot chat only")

        # Login and bot resolution are done above, BEFORE this sleep, so the
        # very first "/start challenge_<N>" send below happens as close to
        # the activation time as possible -- not delayed by connecting to
        # Telegram or resolving the bot entity after the clock hits it.
        if now < open_time:
            sleep_seconds = (open_time - datetime.now(timezone.utc)).total_seconds()
            if sleep_seconds > 0:
                log("Startup check", "INFO",
                    f"[{mode_label}] logged in and ready; waiting {int(sleep_seconds)}s until "
                    f"{open_time_display} before messaging the bot")
                await asyncio.sleep(sleep_seconds)
        else:
            log("Startup check", "INFO",
                f"[{mode_label}] started at {now.strftime('%H:%M:%S')} UTC, at/after {open_time_display} "
                f"-- messaging the bot now")

        # ---- Stage 1 + 2: message the bot, retry if not active yet, wait for Start Quiz ----
        # No more listening on any channel -- we go straight to the bot the
        # same way a real tap on the channel's Join button would have,
        # replicating exactly what click_button_or_follow_deep_link() did
        # for a URL button: send "/start challenge_<N>" to the bot.
        #
        # The bot may reply "This challenge is not active yet." if we're a
        # beat early -- we keep resending until either the "Start Quiz"
        # button shows up or the deadline passes. In test mode, test_bot.py
        # enforces the same activation-time gate, so this exercises the
        # exact same retry behavior as a real run, not a simulation of it.
        #
        # IMPORTANT (derived from live run_challenge.py debug logs on two
        # separate accounts, 2026-09-04): the Welcome message and Question 1
        # arrived 0-1 seconds apart in both runs -- both well before the
        # script's own Start Quiz click completed (~15s later in both
        # runs). That ordering means this bot does not wait for the
        # "Start Quiz" button to be tapped before sending Q1 -- it sends
        # both essentially together. test_bot.py models the opposite (Q1
        # sent once the button callback is received), which is why test
        # mode never exposed this. If we only start listening for Q1 after
        # finding/clicking the Start Quiz button, a Q1 that arrived earlier
        # in the SAME retry loop that found that button is missed entirely
        # -- the listener starts too late to see it, and the run hangs
        # until QUIZ_TIMEOUT_MINUTES with nothing left to arrive.
        #
        # Fix: register a listener for "the first message that isn't a
        # rejection and doesn't have a Start-Quiz-style button" BEFORE
        # sending the very first "/start" at all -- not just before the
        # click. That covers every timing this bot might use: Q1 sent in
        # the same burst as the welcome message (covered because we're
        # already listening before /start goes out), Q1 sent only after
        # the click (still covered, arrives at an already-registered
        # listener), or anything in between.
        first_question_fut = asyncio.get_event_loop().create_future()

        async def _first_question_handler(event):
            if first_question_fut.done():
                return
            msg = event.message
            # Require this to actually look like a question message: real
            # answer-option buttons (plural -- an MCQ has several) AND text
            # matching QUESTION_TEXT_PATTERN, not just "has buttons and
            # isn't the Start-Quiz message." The earlier, purely-negative
            # version of this check would have latched onto ANY buttoned
            # message that wasn't a rejection or the Welcome message as
            # "Question 1" -- e.g. an unrelated announcement or a "join our
            # channel" prompt landing in the same chat before Q1 actually
            # arrives. Rejections like "CHALLENGE CLOSED" have no buttons
            # at all (confirmed in a live debug log) so they were already
            # excluded either way, but this is a meaningfully stricter,
            # positive-signal match rather than relying on exclusion alone.
            if not msg.buttons or len(extract_mcq_options(msg)) < 2:
                return
            if find_button_by_hints(msg, START_QUIZ_TEXT_HINTS) is not None:
                return  # this is the welcome/"Start Quiz" message itself -- ignore
            if not re.search(QUESTION_TEXT_PATTERN, msg.text or "", re.IGNORECASE):
                return
            first_question_fut.set_result(event)

        client.add_event_handler(_first_question_handler, events.NewMessage(chats=challenge_bot))

        try:
            start_command = f"/start challenge_{CHALLENGE_NUMBER}"
            start_quiz_message, loc, _quiz_started_at = await message_bot_with_retry_until_active(
                client,
                challenge_bot,
                start_command,
                START_QUIZ_TEXT_HINTS,
                deadline,
                RETRY_INTERVAL_SECONDS,
                "Message bot / wait for Start Quiz",
            )

            # From here on, use a fresh deadline for the quiz-answering
            # phase -- it must not be truncated to whatever time was left
            # on the Stage 1/2 gating deadline above (e.g. 17:05 UTC in
            # real mode), since the quiz itself can legitimately run past
            # that clock time once started.
            quiz_deadline = datetime.now(timezone.utc) + timedelta(minutes=QUIZ_TIMEOUT_MINUTES)

            await click_button_or_follow_deep_link(client, start_quiz_message, loc[0], loc[1], "Click Start Quiz", click_mode=START_QUIZ_CLICK_MODE)
            # Anchor for MAX_QUIZ_SECONDS (control/pacing_<account>.json):
            # taken right at the click itself, not any response to it -- in
            # fire_and_forget mode there may be no prompt response to
            # anchor to at all, and even in "await" mode the response time
            # is itself variable, so the click is the only stable,
            # deterministic "quiz start" moment.
            quiz_click_at = time.monotonic()
            log("Click Start Quiz", "OK", f"timer started")

            # ---- Stage 3: answer each question ----
            for q_num in range(1, TOTAL_QUESTIONS + 1):
                stage = f"Question {q_num}/{TOTAL_QUESTIONS}"

                if q_num == 1:
                    # May already be resolved (message arrived before or
                    # during the click above) -- wait_for below returns
                    # immediately in that case instead of blocking.
                    remaining = (quiz_deadline - datetime.now(timezone.utc)).total_seconds()
                    if remaining <= 0:
                        raise StageFailure(f"{stage}: wait for question", "deadline already passed")
                    log(f"{stage}: wait for question", "START", f"waiting up to {int(remaining)}s")
                    try:
                        q_event = await asyncio.wait_for(first_question_fut, timeout=remaining)
                        log(f"{stage}: wait for question", "OK", "message received")
                    except asyncio.TimeoutError:
                        log(f"{stage}: wait for question", "TIMEOUT",
                            f"no matching message before deadline ({quiz_deadline.isoformat()})")
                        raise StageFailure(f"{stage}: wait for question", "timed out waiting for message")
                else:
                    q_event = await wait_for_event_with_deadline(
                        client,
                        events.NewMessage(chats=challenge_bot),
                        quiz_deadline,
                        f"{stage}: wait for question",
                    )
                q_message: Message = q_event.message
                question_received_at = time.monotonic()

                options = extract_mcq_options(q_message)
                if not options:
                    raise StageFailure(stage, "message received but no answer buttons found")

                question_text = q_message.text or ""
                bare_letters = _options_are_bare_letters(options)
                log(stage, "INFO",
                    f"parsed {len(options)} options"
                    + (" (bare-letter buttons -- real option text is in the question)" if bare_letters else ""))

                # ask_ai_for_answer() is a blocking, synchronous call (network
                # I/O + time.sleep on retry). Run it in a worker thread so it
                # doesn't freeze this event loop -- otherwise Telethon can't
                # process anything else (including the eventual button click)
                # until the call returns, which is what caused the apparent
                # "stall" on Question 4.
                answer_letter = await asyncio.to_thread(
                    ask_ai_for_answer, question_text, options, stage, q_num
                )
                answer_index = _LETTERS.index(answer_letter)
                if bare_letters:
                    # options[answer_index] is just the letter itself here
                    # (e.g. "A") -- not real content, so don't present it as
                    # if it were the chosen option's text; say plainly that
                    # the real text lives in the question message instead.
                    log(stage, "INFO", f"{AI_PROVIDER.capitalize()}'s answer: {answer_letter} (option text is in the question above, not the button)")
                else:
                    answer_text = _strip_redundant_letter_prefix(options[answer_index], answer_letter) if answer_index < len(options) else "?"
                    log(stage, "INFO", f"{AI_PROVIDER.capitalize()}'s answer: {answer_letter}) {answer_text}")

                # Per-question pacing floor (control/pacing_<account>.json):
                # hold the click back until at least this question's
                # configured number of seconds have passed since its
                # message arrived. Anchored to message receipt (not e.g.
                # Groq's response time) so a slow Groq call -- including a
                # rate-limit backoff -- already counts toward the floor;
                # the sleep below only fires when Groq answered faster
                # than the floor allows. Applies to every question now,
                # not just the last one (see 2026-09-06 decision in
                # context.json).
                #
                # Capped by MAX_QUIZ_SECONDS: the wait is trimmed (never
                # extended) so total time since the Start Quiz click never
                # exceeds that ceiling -- e.g. if 28s have passed and this
                # question's floor would reach 33s, it only waits 2s (to
                # reach 30s), not the full floor. If the ceiling has
                # already been reached, this question isn't paced at all.
                # Both log lines below include the underlying numbers
                # (floor, elapsed, quiz budget used) so pacing_SAF.json /
                # pacing_ETH.json can be tuned from the log alone, without
                # re-deriving them from raw timestamps (added 2026-09-07).
                elapsed_since_question = time.monotonic() - question_received_at
                floor = PACING_SECONDS_BY_QUESTION[q_num]
                elapsed_since_quiz_start = time.monotonic() - quiz_click_at
                remaining_quiz_budget = MAX_QUIZ_SECONDS - elapsed_since_quiz_start
                wait_for = min(floor - elapsed_since_question, remaining_quiz_budget)
                if wait_for > 0:
                    capped_note = " (capped by MAX_QUIZ_SECONDS)" if wait_for < floor - elapsed_since_question else ""
                    log(
                        stage,
                        "INFO",
                        f"pacing: waiting {wait_for:.1f}s before click{capped_note} "
                        f"(floor={floor:.1f}s, {elapsed_since_question:.1f}s already elapsed for this "
                        f"question; quiz budget: {elapsed_since_quiz_start:.1f}s/{MAX_QUIZ_SECONDS:.1f}s used)",
                    )
                    await asyncio.sleep(wait_for)
                elif elapsed_since_question < floor:
                    log(
                        stage,
                        "INFO",
                        f"pacing: skipping wait -- MAX_QUIZ_SECONDS ({MAX_QUIZ_SECONDS:.1f}s) already reached "
                        f"({elapsed_since_quiz_start:.1f}s elapsed since quiz start; this question's floor was "
                        f"{floor:.1f}s, only {elapsed_since_question:.1f}s had passed)",
                    )

                # Buttons were flattened row-by-row in extract_mcq_options; map
                # the flat index back to (row, col) for the click.
                flat_idx = 0
                clicked = False
                for row_idx, row in enumerate(q_message.buttons):
                    for col_idx, _ in enumerate(row):
                        if flat_idx == answer_index:
                            await click_button_or_follow_deep_link(client, q_message, row_idx, col_idx, stage, click_mode=ANSWER_CLICK_MODE)
                            clicked = True
                            break
                        flat_idx += 1
                    if clicked:
                        break

                if not clicked:
                    raise StageFailure(stage, "failed to map answer letter to a button position")

                log(stage, "OK", f"clicked option {answer_letter}")
        finally:
            client.remove_event_handler(_first_question_handler, events.NewMessage(chats=challenge_bot))

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
        if _background_click_tasks:
            # Give any fire_and_forget click(s) a bounded chance to finish
            # and log their actual duration before we tear down the
            # connection out from under them. 20s is comfortably above the
            # ~15s we've measured live, with headroom; if it's still
            # pending after that, disconnect anyway rather than hang the
            # job -- the click_mode=await path remains available if this
            # data is critical to capture reliably.
            pending = [t for t in _background_click_tasks if not t.done()]
            if pending:
                log("Cleanup", "INFO", f"waiting up to 20s for {len(pending)} background click(s) to finish")
                await asyncio.wait(pending, timeout=20)
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
