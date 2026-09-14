"""
Bulk AI-answer tester against a hand-maintained question bank, plus a
"deep investigate" mode for seeing WHY a specific already-known-wrong
question was missed.

Two modes, both driven by the MODE env var:

  MODE=bulk (default)
    Loads a question bank JSON file (see question_banks/section_10_bank_test.json
    for the schema), answers every question using the REAL answering logic
    from run_challenge.py (same ask_ai_for_answer(), same prompt-building,
    same provider/model/round-robin config as a live run -- imported
    directly, not reimplemented), and reports ONLY the questions it got
    wrong: bank id, the AI's answer, and the correct answer. Nothing is
    sent to Telegram -- this only calls the AI provider.

  MODE=investigate
    Takes a comma-separated list of bank ids (QUESTION_IDS env var --
    the same ids you saw in a prior bulk-mode miss report) and re-asks
    the AI just those questions, this time with a short reasoning
    alongside the answer (ask_ai_for_answer_explained()), so you can see
    how it got there. Also one-off diagnostic calls, not part of the
    timed live flow, so no retry loop -- a StageFailure on one question
    is reported and the rest continue.

Run this from the "Test AI On Bank" workflow in the Actions tab (uses the
same repo secrets as the other workflows) -- results appear on the job's
summary page. Not meant to be run locally, same as test_gemini_on_history.py.
"""

import json
import os

# Reuses the exact same prompt-building, model call, parsing, and provider
# dispatch logic the live flow uses -- so this tests the real thing, not a
# copy of it. See test_gemini_on_history.py for the same pattern.
from run_challenge import (
    ask_ai_for_answer,
    ask_ai_for_answer_explained,
    StageFailure,
    AI_PROVIDER,
    GEMINI_MODEL,
    GROQ_MODEL,
    OPENROUTER_MODEL,
)

MODE = os.environ.get("MODE", "bulk").strip().lower()
BANK_FILE = os.environ.get("BANK_FILE", "").strip()
QUESTION_IDS_RAW = os.environ.get("QUESTION_IDS", "").strip()

SUMMARY_PATH = os.environ.get("GITHUB_STEP_SUMMARY")

_LETTERS = ["A", "B", "C", "D", "E", "F"]


def emit(lines: list[str]):
    """Print to the normal job log AND append to the Actions job summary."""
    text = "\n".join(lines)
    print(text)
    if SUMMARY_PATH:
        with open(SUMMARY_PATH, "a") as f:
            f.write(text + "\n")


def _current_model_label() -> str:
    if AI_PROVIDER == "groq":
        return f"Groq ({GROQ_MODEL})"
    if AI_PROVIDER == "openrouter":
        return f"OpenRouter ({OPENROUTER_MODEL})"
    return f"Gemini ({GEMINI_MODEL})"


def load_bank(path: str) -> dict:
    """
    Loads a question bank JSON file and returns {id: question_dict}.
    Keys are normalized to str so ids can be matched consistently whether
    the bank file uses numbers or strings and whether QUESTION_IDS was
    typed with or without surrounding spaces.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError(f"{path}: expected a top-level \"questions\" list with at least one entry")

    by_id = {}
    for i, q in enumerate(questions):
        if "id" not in q:
            raise ValueError(f"{path}: question at position {i} is missing required field \"id\"")
        qid = str(q["id"])
        if qid in by_id:
            raise ValueError(f"{path}: duplicate question id {qid!r} -- ids must be unique")
        for field in ("text", "options", "correct"):
            if field not in q:
                raise ValueError(f"{path}: question id {qid!r} is missing required field {field!r}")
        by_id[qid] = q
    return by_id


def format_option(q: dict, option_index: int) -> str:
    """
    Same rendering rule as test_bot.py's format_option(): for bare_letters
    questions, q["options"][i] is just the bare letter itself (real text
    is embedded in q["text"]), so showing it again adds nothing -- show
    only the letter. Otherwise show "A) <real text>".
    """
    letter = _LETTERS[option_index] if option_index < len(_LETTERS) else "?"
    if q.get("bare_letters"):
        return letter
    options = q.get("options") or []
    if 0 <= option_index < len(options):
        return f"{letter}) {options[option_index]}"
    return letter


def run_bulk(bank: dict):
    emit([
        f"## Bulk AI answer test — {BANK_FILE}",
        "",
        f"Model: `{_current_model_label()}`",
        f"Questions in bank: {len(bank)}",
        "",
        "_Diagnostic only -- nothing is sent to the challenge bot._",
        "",
    ])

    misses = []
    errors = []
    # q_num drives Groq/Gemini's round-robin across configured accounts --
    # same mechanism run_challenge.py uses for the live 5-question flow,
    # just extended naturally to however many questions the bank has.
    for q_num, (qid, q) in enumerate(bank.items(), start=1):
        options = q["options"]
        try:
            letter = ask_ai_for_answer(q["text"], options, f"bank id {qid}", q_num)
        except StageFailure as e:
            errors.append((qid, e.detail))
            continue

        correct_index = q["correct"]
        correct_letter = _LETTERS[correct_index] if correct_index < len(_LETTERS) else "?"
        if letter != correct_letter:
            misses.append({
                "id": qid,
                "ai_letter": letter,
                "ai_text": format_option(q, _LETTERS.index(letter)) if letter in _LETTERS else letter,
                "correct_letter": correct_letter,
                "correct_text": format_option(q, correct_index),
            })

    total_attempted = len(bank) - len(errors)
    correct_count = total_attempted - len(misses)
    emit([f"**Score: {correct_count}/{total_attempted}**" + (f" ({len(errors)} failed to answer)" if errors else ""), ""])

    if misses:
        emit(["| Bank ID | AI answered | Correct answer |", "|---|---|---|"])
        for m in misses:
            emit([f"| {m['id']} | {m['ai_text']} | {m['correct_text']} |"])
        emit([""])
        ids_list = ", ".join(str(m["id"]) for m in misses)
        emit([f"To see reasoning for any of these, rerun this workflow with mode=investigate and question_ids=`{ids_list}`.", ""])
    else:
        emit(["No misses. 🎉", ""])

    if errors:
        emit(["### Questions that failed to get an answer at all", ""])
        for qid, detail in errors:
            emit([f"- Bank ID {qid}: {detail}"])
        emit([""])


def run_investigate(bank: dict):
    if not QUESTION_IDS_RAW:
        raise SystemExit(
            "MODE=investigate requires QUESTION_IDS (comma-separated bank ids from a prior bulk-mode miss report)."
        )
    requested_ids = [x.strip() for x in QUESTION_IDS_RAW.split(",") if x.strip()]

    emit([
        f"## Deep investigation — {BANK_FILE}",
        "",
        f"Model: `{_current_model_label()}`",
        f"Investigating {len(requested_ids)} question(s): {', '.join(requested_ids)}",
        "",
    ])

    for q_num, qid in enumerate(requested_ids, start=1):
        q = bank.get(qid)
        if q is None:
            emit([f"### Bank ID {qid}", "", f"⚠️ Not found in {BANK_FILE}.", ""])
            continue

        options = q["options"]
        correct_index = q["correct"]
        correct_letter = _LETTERS[correct_index] if correct_index < len(_LETTERS) else "?"

        emit([f"### Bank ID {qid}", "", f"**Question:** {q['text']}", ""])
        try:
            letter, reasoning = ask_ai_for_answer_explained(q["text"], options, q_num)
        except StageFailure as e:
            emit([f"⚠️ Failed to get a reasoned answer: {e.detail}", ""])
            continue

        ai_text = format_option(q, _LETTERS.index(letter)) if letter in _LETTERS else letter
        correct_text = format_option(q, correct_index)
        verdict = "✅ Correct" if letter == correct_letter else "❌ Wrong"

        emit([
            f"- AI answered: **{ai_text}** ({verdict})",
            f"- Correct answer: **{correct_text}**",
            f"- Reasoning: {reasoning or '_(none returned)_'}",
            "",
        ])


def main():
    if not BANK_FILE:
        raise SystemExit("BANK_FILE is required -- path to a question bank JSON, e.g. question_banks/section_10_bank_test.json")
    bank = load_bank(BANK_FILE)

    if MODE == "investigate":
        run_investigate(bank)
    elif MODE == "bulk":
        run_bulk(bank)
    else:
        raise SystemExit(f"MODE must be 'bulk' or 'investigate', got {MODE!r}")


if __name__ == "__main__":
    main()
