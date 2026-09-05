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
from groq import Groq


# ----------------------------------------------------------------------
# Configuration (all from environment variables / GitHub Secrets)
# ----------------------------------------------------------------------

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_STRING = os.environ["TG_SESSION"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")  # only required if AI_PROVIDER=groq

# Which AI answers the quiz questions. "groq" is the fast path (Groq's LPU
# hardware gives far more consistent low latency than Gemini has shown in
# testing); "gemini" is kept available as a fallback / for comparison.
AI_PROVIDER = os.environ.get("AI_PROVIDER", "groq").lower()
if AI_PROVIDER not in ("groq", "gemini"):
    raise SystemExit(f"AI_PROVIDER must be 'groq' or 'gemini', got {AI_PROVIDER!r}")
if AI_PROVIDER == "groq" and not GROQ_API_KEY:
    raise SystemExit("AI_PROVIDER=groq requires GROQ_API_KEY to be set.")

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

# ---- Stage 1 timing ----
# The bot rejects /start before it opens, replying with the exact text in
# CHALLENGE_NOT_ACTIVE_TEXT. We don't message the bot at all until the open
# time, and we refuse to run entirely if started too early -- this script
# isn't meant to sit idle for a long time waiting.
#
# Real mode and test mode both follow this exact same shape, just anchored
# to a different open time:
#   - real mode  -> CHALLENGE_OPEN_TIME_UTC        (default 17:00 UTC, the
#                    real challenge's actual go-live time)
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
CHALLENGE_OPEN_TIME_UTC = os.environ.get("CHALLENGE_OPEN_TIME_UTC", "17:00")          # HH:MM, UTC -- real mode
TEST_ACTIVATION_TIME_UTC = os.environ.get("TEST_ACTIVATION_TIME_UTC")                # HH:MM, UTC -- test mode
EARLIEST_RUN_MINUTES_BEFORE_OPEN = float(os.environ.get("EARLIEST_RUN_MINUTES_BEFORE_OPEN", "15"))
RETRY_WINDOW_MINUTES_AFTER_OPEN = float(os.environ.get("RETRY_WINDOW_MINUTES_AFTER_OPEN", "5"))
RETRY_INTERVAL_SECONDS = float(os.environ.get("RETRY_INTERVAL_SECONDS", "2"))

OPEN_TIME_UTC = TEST_ACTIVATION_TIME_UTC if TEST_MODE else CHALLENGE_OPEN_TIME_UTC
if TEST_MODE and not OPEN_TIME_UTC:
    raise SystemExit(
        "TEST_ACTIVATION_TIME_UTC is required in test mode -- set it to the same "
        "activation time (HH:MM, UTC) you gave the 'Run Test Bot' workflow."
    )

# Once Start Quiz has been clicked, this is the separate time budget for the
# quiz-answering phase (Stage 3) -- independent of the Stage 1/2 gating above,
# since answering can legitimately run past the Stage 1/2 deadline once started.
QUIZ_TIMEOUT_MINUTES = float(os.environ.get("QUIZ_TIMEOUT_MINUTES", "15"))

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# openai/gpt-oss-20b is Groq's fastest current production model (~1000
# tokens/sec) that also supports strict JSON Schema output -- see
# https://console.groq.com/docs/model/openai/gpt-oss-20b. Groq deprecated
# llama-3.1-8b-instant (the previous fast/cheap option) on free and
# developer tiers in August 2026; this is its recommended replacement.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")


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
    raise SystemExit(f"THINKING_LEVEL must be one of {_VALID_THINKING_LEVELS}, got {THINKING_LEVEL!r}")

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


def today_utc_at(hh_mm: str) -> datetime:
    """Parses 'HH:MM' into a UTC datetime for the current UTC calendar day."""
    hour, minute = (int(p) for p in hh_mm.split(":"))
    now = datetime.now(timezone.utc)
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ----------------------------------------------------------------------
# Gemini / Groq: ask which option letter is correct, with strict output + retry
# ----------------------------------------------------------------------

_gemini_client = genai.Client(api_key=GEMINI_API_KEY)
_groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

_LETTERS = ["A", "B", "C", "D", "E", "F"]  # supports up to 6 options, just in case


def _build_prompt(question_text: str, options: list[str]) -> str:
    lettered = "\n".join(f"{_LETTERS[i]}) {opt}" for i, opt in enumerate(options))
    extra = f" {PROMPT_EXTRA_INSTRUCTION}" if PROMPT_EXTRA_INSTRUCTION else ""
    # Section reference notes (if SECTION_NOTES is set) go in full ahead of
    # the question -- same block reused for every question this run, not
    # matched per-question. See SECTION_NOTES_TEXT / _load_section_notes above.
    notes_block = f"Reference notes for this section:\n{SECTION_NOTES_TEXT}\n\n" if SECTION_NOTES_TEXT else ""
    return (
        f"{notes_block}"
        "You are answering a multiple-choice question. "
        "Respond with ONLY the single letter of the correct option. "
        f"No words, no punctuation, no explanation — just the letter.{extra}\n\n"
        f"Question: {question_text}\n\n"
        f"Options:\n{lettered}\n\n"
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


def ask_groq_for_answer(question_text: str, options: list[str], attempt_label: str) -> str:
    """
    Groq equivalent of ask_gemini_for_answer(). Same shape, same return
    value (a single option letter), so the call site doesn't need to know
    which provider is in use.
    """
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
                return _groq_client.chat.completions.create(**groq_kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_attempts:
                    log(
                        f"Groq answer ({attempt_label})",
                        "INFO",
                        f"API call failed ({e.__class__.__name__}), retrying (attempt {attempt}/{max_attempts})",
                    )
                    time.sleep(0.5)
        raise StageFailure(
            f"Groq answer ({attempt_label})",
            f"Groq API call failed after {max_attempts} attempts: {last_error}",
        )

    def _try_once():
        resp = _call_groq()
        _log_groq_usage(resp, attempt_label)
        raw = (resp.choices[0].message.content or "").strip()
        try:
            import json as _json
            parsed = _json.loads(raw)
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


def ask_ai_for_answer(question_text: str, options: list[str], attempt_label: str) -> str:
    """Dispatches to whichever provider AI_PROVIDER selects."""
    if AI_PROVIDER == "groq":
        return ask_groq_for_answer(question_text, options, attempt_label)
    return ask_gemini_for_answer(question_text, options, attempt_label)


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
    _install_telethon_flood_wait_logging()

    now = datetime.now(timezone.utc)

    open_time = today_utc_at(OPEN_TIME_UTC)
    earliest_run_time = open_time - timedelta(minutes=EARLIEST_RUN_MINUTES_BEFORE_OPEN)
    deadline = open_time + timedelta(minutes=RETRY_WINDOW_MINUTES_AFTER_OPEN)
    mode_label = "TEST MODE" if TEST_MODE else "real mode"

    if now < earliest_run_time:
        raise StageFailure(
            "Startup check",
            f"[{mode_label}] it's {now.strftime('%H:%M:%S')} UTC, which is more than "
            f"{EARLIEST_RUN_MINUTES_BEFORE_OPEN:.0f} minutes before the {OPEN_TIME_UTC} UTC activation "
            f"time. Trigger the workflow again closer to {OPEN_TIME_UTC} UTC.",
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
                    f"{OPEN_TIME_UTC} UTC before messaging the bot")
                await asyncio.sleep(sleep_seconds)
        else:
            log("Startup check", "INFO",
                f"[{mode_label}] started at {now.strftime('%H:%M:%S')} UTC, at/after {OPEN_TIME_UTC} UTC "
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
        # IMPORTANT (confirmed on the live BirrForex bot via a user
        # screenshot + debug logs on 2026-09-04): this bot sends Question 1
        # immediately alongside the welcome/"Start Quiz" message -- both
        # timestamped the same minute -- NOT gated on the "Start Quiz"
        # button being clicked. test_bot.py models the opposite (Q1 is only
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
            # Require this to actually look like a question message (has
            # answer-option buttons), not just "isn't a rejection and isn't
            # the Start-Quiz message." Rejections like "CHALLENGE CLOSED"
            # for an earlier/expired challenge_N have no buttons at all
            # (confirmed in a live debug log), so they're already excluded
            # by requiring message.buttons -- but being explicit here also
            # protects against a future bot reply that has buttons without
            # being Q1 (e.g. a "join our channel" prompt).
            if not msg.buttons:
                return
            if find_button_by_hints(msg, START_QUIZ_TEXT_HINTS) is not None:
                return  # this is the welcome/"Start Quiz" message itself -- ignore
            first_question_fut.set_result(event)

        client.add_event_handler(_first_question_handler, events.NewMessage(chats=challenge_bot))

        try:
            start_command = f"/start challenge_{CHALLENGE_NUMBER}"
            start_quiz_message, loc = await message_bot_with_retry_until_active(
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

            await click_button_or_follow_deep_link(client, start_quiz_message, loc[0], loc[1], "Click Start Quiz")
            start_quiz_click_time = time.monotonic()
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

                options = extract_mcq_options(q_message)
                if not options:
                    raise StageFailure(stage, "message received but no answer buttons found")

                question_text = q_message.text or ""
                log(stage, "INFO", f"parsed {len(options)} options")

                # ask_ai_for_answer() is a blocking, synchronous call (network
                # I/O + time.sleep on retry). Run it in a worker thread so it
                # doesn't freeze this event loop -- otherwise Telethon can't
                # process anything else (including the eventual button click)
                # until the call returns, which is what caused the apparent
                # "stall" on Question 4.
                answer_letter = await asyncio.to_thread(
                    ask_ai_for_answer, question_text, options, stage
                )
                answer_index = _LETTERS.index(answer_letter)
                answer_text = options[answer_index] if answer_index < len(options) else "?"
                log(stage, "INFO", f"{AI_PROVIDER.capitalize()}'s answer: {answer_letter}) {answer_text}")

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
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
