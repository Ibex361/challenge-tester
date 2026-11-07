# Pacing configuration (`pacing_<ACCOUNT>.json`)

The workflow's `account` input selects the file: `SAF`, `ETH`, `Ab62` or `Ab82`.

```json
{
  "default": 3,
  "1": 2.1,
  "2": 1.9,
  "3": 2.3,
  "4": 3.3,
  "5": 5.5,
  "max_quiz_seconds": 19.9
}
```

- `"1"`, `"2"`, ... are question numbers. The value is the minimum number of seconds between a question
  arriving and its answer being clicked. AI thinking time counts towards it.
- `"default"` applies to every question without its own key, including questions beyond the fifth.
- `"max_quiz_seconds"` is a ceiling on the total time since the account's **challenge open time**
  (`challenge_open_time.json`), not since the Start Quiz click. A slow reply to `/start` therefore uses up
  part of the budget instead of adding to it. Each pacing wait is shortened so it never pushes the total past
  this value. AI and network time are not capped, so the real total can still exceed it.
- Keys starting with `_` (such as `_comment`) are notes and are ignored by the script.

Tip: use quick paces for the first ~3 questions. If later questions are harder, slow paces on the easy early
ones give the worst of both worlds.

In the example above the paces add up to 2.1 + 1.9 + 2.3 + 3.3 + 5.5 = 15.1s, so the total is bounded
between roughly 15.1s and 19.9s.
